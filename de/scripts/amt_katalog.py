"""Úřední otevřená data o Kitách (Kita Finder): katalog a stažení.

Projde GovData (celoněmecký katalog otevřených dat, sklízí portály zemí a měst) a pár
regionálních katalogů, najde datové sady se seznamy Kit a stáhne z nich tabulky
(CSV, XLSX, JSON, GeoJSON, WFS, OGC API, ArcGIS). Bereme jen sady s otevřenou licencí.

Výstup:
  de/data/amt/katalog.json          – všechny nalezené sady a zdroje (i přeskočené, s důvodem)
  de/data/amt/raw/<id>.json.gz      – {meta, sloupce, radky} pro každý použitelný seznam zařízení
Párování na Kity z OSM a výběr sloupců se dělá až v dalším kroku (amt_import.py).
"""
import csv, gzip, hashlib, io, json, os, re, sys, time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

import requests

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "amt")
RAW = os.path.join(OUT, "raw")
UA = {"User-Agent": "KitaFinderBot/1.0 (+https://github.com/ondrejpsenicka26/rodicovnik-fotky; offene Daten)",
      "Accept-Language": "de-DE,de;q=0.9"}
MAX_BAJTU = 60 * 1024 * 1024
MAX_RADKU = 40000
ROZPOCET = 90 * 1024 * 1024  # součet uložených gz souborů

CKAN = [
    ("govdata", "https://ckan.govdata.de/api/3/action/package_search"),
    ("govdata", "https://www.govdata.de/ckan/api/3/action/package_search"),
    ("berlin", "https://datenregister.berlin.de/api/3/action/package_search"),
    ("hamburg", "https://suche.transparenz.hamburg.de/api/3/action/package_search"),
    ("nrw", "https://ckan.open.nrw.de/api/3/action/package_search"),
    ("sh", "https://opendata.schleswig-holstein.de/api/3/action/package_search"),
    ("muenchen", "https://opendata.muenchen.de/api/3/action/package_search"),
    ("koeln", "https://offenedaten-koeln.de/api/3/action/package_search"),
    ("leipzig", "https://opendata.leipzig.de/api/3/action/package_search"),
    ("duesseldorf", "https://opendata.duesseldorf.de/api/3/action/package_search"),
    ("frankfurt", "https://offenedaten.frankfurt.de/api/3/action/package_search"),
    ("bayern", "https://open.bydata.de/api/hub/search/ckan/package_search"),
    ("bw", "https://www.daten-bw.de/ckan/api/3/action/package_search"),
    ("rlp", "https://daten.rlp.de/api/3/action/package_search"),
    ("sachsen", "https://www.opendata.sachsen.de/api/3/action/package_search"),
    ("thueringen", "https://opendata.thueringen.de/api/3/action/package_search"),
    ("brandenburg", "https://datenadler.de/api/3/action/package_search"),
    ("mv", "https://www.opendata-mv.de/api/3/action/package_search"),
    ("bremen", "https://daten.bremen.de/api/3/action/package_search"),
    ("rostock", "https://www.opendata-hro.de/api/3/action/package_search"),
    ("bonn", "https://opendata.bonn.de/api/3/action/package_search"),
    ("dortmund", "https://open-data.dortmund.de/api/3/action/package_search"),
    ("essen", "https://opendata.essen.de/api/3/action/package_search"),
    ("karlsruhe", "https://transparenz.karlsruhe.de/api/3/action/package_search"),
    ("stuttgart", "https://opendata.stuttgart.de/api/3/action/package_search"),
    ("dresden", "https://opendata.dresden.de/informationsportal/api/3/action/package_search"),
]
HLEDAT = ["Kita", "Kitas", "Kindertagesstätten", "Kindertagesstätte", "Kindertageseinrichtungen",
          "Kindertageseinrichtung", "Kindergärten", "Kindergarten", "Kindertagesbetreuung", "Kinderbetreuung",
          "Kinderkrippen", "Krippen", "Kinderbetreuungseinrichtungen", "Tageseinrichtungen für Kinder", "Kitaverzeichnis",
          "Kita-Verzeichnis", "Kindertagespflege", "Kitaliste", "Kitafinder", "Kita-Navigator"]
R_KITA = re.compile(r"kita|kindertages|kindergarten|kindergärten|kinderg(ae|ä)rten|krippe|kinderbetreu|"
                    r"tageseinrichtung|tagespflege|kinderhaus|kinderhäuser|hort", re.I)
# čistě statistické sady (počty dětí, kvóty, výdaje) – seznam zařízení v nich nebývá
R_STATISTIKA = re.compile(r"betreuungsquote|quote\b|statistik|anzahl der|kinder in tages|betreute kinder|"
                          r"ausgaben|personal in|schlüssel|prognose|bedarfsplan|bev(ö|oe)lkerung|gebühren|"
                          r"beitr(a|ä)ge|haushalt|zeitreihe|indikator|monitoring", re.I)
OTEVRENE = re.compile(r"dl-de|dl-by|dl-zero|cc-?by|cc-?zero|cc0|ccby|odc-|odbl|pddl|creative ?commons|"
                      r"datenlizenz|public ?domain|gemeinfrei|officialwork|geonutz|other-open|notspecified-open", re.I)
FORMATY = {"csv", "xlsx", "xls", "json", "geojson", "wfs", "tsv", "txt", "xml", "gml", "api", "ogc api", "arcgis",
           "esri rest", "rest", "ods", "ogc_api_features", "ogc api features", "oaf"}


def log(*a):
    print(*a, flush=True)


def get(url, **kw):
    kw.setdefault("timeout", 60)
    kw.setdefault("headers", UA)
    for pokus in range(3):
        try:
            r = requests.get(url, **kw)
            if r.status_code in (429, 502, 503, 504) and pokus < 2:
                time.sleep(4 * (pokus + 1))
                continue
            return r
        except Exception as e:  # noqa: BLE001
            if pokus == 2:
                raise
            time.sleep(3)
    return None


# ---------- katalog ----------
def hledej_ckan(jmeno, api):
    nalezene, ok = {}, False
    for q in HLEDAT:
        start = 0
        while start < 3000:
            try:
                r = get(api, params={"q": q, "rows": 500, "start": start}, timeout=45)
                if r is None or r.status_code != 200:
                    break
                d = r.json()
                res = d.get("result") or {}
                vys = res.get("results") or []
            except Exception as e:  # noqa: BLE001
                log(f"  ! {jmeno} {q}: {str(e)[:120]}")
                break
            ok = True
            for p in vys:
                nalezene[p.get("id") or p.get("name")] = p
            start += 500
            if len(vys) < 500:
                break
    log(f"katalog {jmeno} ({urlparse(api).netloc}): {'ok' if ok else 'nedostupný'}, {len(nalezene)} sad")
    return nalezene, ok


def text_pole(x):
    if isinstance(x, dict):
        return x.get("de") or x.get("en") or next(iter(x.values()), "") or ""
    if isinstance(x, list):
        return " ".join(str(text_pole(i)) for i in x)
    return str(x or "")


def sada_info(zdroj, p):
    org = p.get("organization") or {}
    extras = {e.get("key"): e.get("value") for e in (p.get("extras") or []) if isinstance(e, dict)}
    lic = p.get("license_id") or p.get("license_title") or ""
    zdroje = []
    for r in p.get("resources") or []:
        if not isinstance(r, dict):
            continue
        url = (r.get("url") or r.get("access_url") or r.get("download_url") or "").strip()
        if isinstance(r.get("url"), list):
            url = r["url"][0] if r["url"] else ""
        rl = r.get("license") or r.get("license_id") or ""
        zdroje.append({"url": url, "format": text_pole(r.get("format")).strip().lower(),
                       "nazev": text_pole(r.get("name") or r.get("title"))[:200], "licence": text_pole(rl)[:120]})
    return {
        "zdroj": zdroj, "id": p.get("id") or p.get("name"), "slug": p.get("name"),
        "titul": text_pole(p.get("title"))[:300], "popis": text_pole(p.get("notes"))[:600],
        "vydavatel": text_pole(org.get("title") or org.get("name") or p.get("author") or p.get("maintainer")
                               or extras.get("publisher_name") or "")[:200],
        "autor": text_pole(p.get("author") or extras.get("publisher_name") or "")[:200],
        "licence": text_pole(lic)[:120], "licenceUrl": text_pole(p.get("license_url"))[:300],
        "web": text_pole(p.get("url") or extras.get("metadata_original_html") or "")[:300],
        "upraveno": text_pole(p.get("metadata_modified") or "")[:25],
        "zdroje": zdroje,
    }


# ---------- čtení tabulek ----------
def dekoduj(b):
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("latin-1", "replace")


def z_csv(b):
    t = dekoduj(b)
    hlava = t[:20000]
    odd = max(";,\t|", key=lambda c: hlava.count(c))
    radky = list(csv.reader(io.StringIO(t), delimiter=odd))
    return z_matice(radky)


def z_matice(radky):
    """Najde řádek záhlaví (první s ≥3 neprázdnými textovými buňkami) a vrátí slovníky."""
    radky = [[("" if c is None else str(c)).strip() for c in r] for r in radky]
    radky = [r for r in radky if any(r)]
    for i, r in enumerate(radky[:30]):
        plne = [c for c in r if c]
        if len(plne) >= 3 and sum(1 for c in plne if not re.fullmatch(r"[\d.,\s-]+", c)) >= 3:
            hl = [c or f"sloupec{j + 1}" for j, c in enumerate(r)]
            # stejná jména sloupců odlišit
            vid = {}
            for j, c in enumerate(hl):
                vid[c] = vid.get(c, 0) + 1
                if vid[c] > 1:
                    hl[j] = f"{c}_{vid[c]}"
            out = []
            for rr in radky[i + 1:i + 1 + MAX_RADKU]:
                out.append({hl[j]: rr[j] for j in range(min(len(hl), len(rr))) if rr[j]})
            return hl, out
    return [], []


def z_xlsx(b):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(b), read_only=True, data_only=True)
    nej = ([], [])
    for ws in wb.worksheets[:8]:
        radky = []
        for i, r in enumerate(ws.iter_rows(values_only=True)):
            if i > MAX_RADKU + 40:
                break
            radky.append(list(r))
        hl, out = z_matice(radky)
        if len(out) > len(nej[1]):
            nej = (hl, out)
    return nej


def z_xls(b):
    import xlrd
    wb = xlrd.open_workbook(file_contents=b)
    nej = ([], [])
    for sh in wb.sheets()[:8]:
        radky = [sh.row_values(i) for i in range(min(sh.nrows, MAX_RADKU + 40))]
        radky = [[(str(int(c)) if isinstance(c, float) and c == int(c) else c) for c in r] for r in radky]
        hl, out = z_matice(radky)
        if len(out) > len(nej[1]):
            nej = (hl, out)
    return nej


def stred(geom):
    """Přibližný střed geometrie GeoJSON (průměr bodů)."""
    if not isinstance(geom, dict):
        return None
    body = []

    def projdi(c):
        if isinstance(c, (list, tuple)) and len(c) >= 2 and all(isinstance(x, (int, float)) for x in c[:2]):
            body.append((c[0], c[1]))
        elif isinstance(c, (list, tuple)):
            for x in c[:400]:
                projdi(x)

    if geom.get("type") == "GeometryCollection":
        for g in geom.get("geometries") or []:
            s = stred(g)
            if s:
                body.append(s)
    else:
        projdi(geom.get("coordinates"))
    if not body:
        return None
    return sum(p[0] for p in body) / len(body), sum(p[1] for p in body) / len(body)


def plochy(d, pref=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            if len(out) < 80:
                out.update(plochy(v, f"{pref}{k}."))
        elif isinstance(v, list):
            if v and all(not isinstance(x, (dict, list)) for x in v):
                out[f"{pref}{k}"] = "; ".join(str(x) for x in v[:12])
        elif v not in (None, ""):
            out[f"{pref}{k}"] = str(v).strip()
    return out


def z_geojson(d):
    out, crs = [], ""
    try:
        crs = str(((d.get("crs") or {}).get("properties") or {}).get("name") or "")
    except Exception:  # noqa: BLE001
        pass
    for f in (d.get("features") or [])[:MAX_RADKU]:
        if not isinstance(f, dict):
            continue
        r = plochy(f.get("properties") or f.get("attributes") or {})
        g = f.get("geometry")
        s = stred(g) if g and "type" in g else None
        if not s and isinstance(g, dict) and "x" in g and "y" in g:  # Esri JSON
            s = (g["x"], g["y"])
        if s:
            r["_x"], r["_y"] = repr(round(s[0], 7)), repr(round(s[1], 7))
        if r:
            out.append(r)
    hl = []
    for r in out[:200]:
        for k in r:
            if k not in hl:
                hl.append(k)
    return hl, out, crs


def z_json(b):
    d = json.loads(dekoduj(b))
    if isinstance(d, dict) and isinstance(d.get("features"), list):
        return z_geojson(d)
    kand = None
    if isinstance(d, list):
        kand = d
    elif isinstance(d, dict):
        fronta = [d]
        while fronta and kand is None:
            x = fronta.pop(0)
            for v in x.values():
                if isinstance(v, list) and len(v) >= 3 and all(isinstance(i, dict) for i in v[:5]):
                    kand = v
                    break
                if isinstance(v, dict):
                    fronta.append(v)
    if not kand:
        return [], [], ""
    out = [plochy(x) for x in kand[:MAX_RADKU] if isinstance(x, dict)]
    hl = []
    for r in out[:200]:
        for k in r:
            if k not in hl:
                hl.append(k)
    return hl, out, ""


def lokalni(tag):
    return tag.rsplit("}", 1)[-1]


def z_gml(b):
    """Obecné čtení GML/XML: každý prvek kolekce = řádek, listy = sloupce."""
    try:
        root = ET.fromstring(b)
    except ET.ParseError:
        return [], [], ""
    out, crs = [], ""
    cleny = [e for e in root.iter() if lokalni(e.tag) in ("featureMember", "member", "featureMembers")]
    prvky = []
    for c in cleny:
        prvky.extend(list(c))
    if not prvky and len(list(root)) >= 3:
        prvky = list(root)
    for f in prvky[:MAX_RADKU]:
        r = {}
        for e in f.iter():
            n = lokalni(e.tag)
            if n in ("pos", "coordinates") and e.text:
                c = re.split(r"[\s,]+", e.text.strip())
                if len(c) >= 2 and "_x" not in r:
                    try:
                        r["_x"], r["_y"] = repr(float(c[0])), repr(float(c[1]))
                    except ValueError:
                        pass
                srs = e.get("srsName") or ""
                crs = crs or srs
                continue
            srs = e.get("srsName")
            if srs and not crs:
                crs = srs
            if len(e) == 0 and e.text and e.text.strip() and n not in r:
                r[n] = e.text.strip()[:400]
        if len(r) >= 2:
            out.append(r)
    hl = []
    for r in out[:200]:
        for k in r:
            if k not in hl:
                hl.append(k)
    return hl, out, crs


# ---------- stažení zdroje ----------
def stahni(url, params=None, limit=MAX_BAJTU):
    r = get(url, params=params, stream=True, timeout=90)
    if r is None or r.status_code != 200:
        return None, f"HTTP {getattr(r, 'status_code', '?')}", ""
    buf = io.BytesIO()
    for kus in r.iter_content(256 * 1024):
        buf.write(kus)
        if buf.tell() > limit:
            return None, "příliš velké", ""
    return buf.getvalue(), "", r.headers.get("content-type", "")


def bez_dotazu(url):
    u = urlparse(url)
    return urlunparse((u.scheme, u.netloc, u.path, "", "", ""))


def wfs(url):
    """Vrátí seznam (typ, sloupce, řádky, crs) pro vrstvy WFS, které vypadají na Kity."""
    zaklad = bez_dotazu(url)
    q = {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items()}
    extra = {k: v for k, v in q.items() if k in ("map", "layers")}
    b, chyba, _ = stahni(zaklad, {**extra, "service": "WFS", "request": "GetCapabilities"}, 8 * 1024 * 1024)
    if not b:
        return [], f"GetCapabilities: {chyba}"
    try:
        root = ET.fromstring(b)
    except ET.ParseError:
        return [], "GetCapabilities není XML"
    verze = root.get("version") or "2.0.0"
    typy = []
    for ft in root.iter():
        if lokalni(ft.tag) != "FeatureType":
            continue
        jm = tit = ""
        for e in ft:
            if lokalni(e.tag) == "Name":
                jm = (e.text or "").strip()
            elif lokalni(e.tag) == "Title":
                tit = (e.text or "").strip()
        if jm:
            typy.append((jm, tit))
    chtene = [t for t in typy if R_KITA.search(f"{t[0]} {t[1]}")]
    if not chtene and len(typy) <= 3:
        chtene = typy
    if q.get("typename") or q.get("typenames"):
        tn = q.get("typenames") or q.get("typename")
        chtene = [(tn, tn)] + [t for t in chtene if t[0] != tn]
    vys = []
    for jm, tit in chtene[:6]:
        ziskano = False
        for of in ("application/json", "application/geo+json", "geojson", "json", "application/json; subtype=geojson", None):
            p = {**extra, "service": "WFS", "request": "GetFeature", "version": verze if verze.startswith(("1.1", "2")) else "1.1.0",
                 ("typeNames" if verze.startswith("2") else "typeName"): jm, "srsName": "EPSG:4326"}
            if of:
                p["outputFormat"] = of
            try:
                b, chyba, ct = stahni(zaklad, p)
            except Exception as e:  # noqa: BLE001
                chyba, b = str(e)[:80], None
            if not b:
                continue
            hl, radky, crs = [], [], ""
            if b.lstrip()[:1] in (b"{", b"["):
                try:
                    hl, radky, crs = z_json(b)
                    crs = crs or "EPSG:4326"
                except Exception:  # noqa: BLE001
                    pass
            elif b.lstrip()[:1] == b"<" and b"ExceptionReport" not in b[:2000] and b"ServiceException" not in b[:2000]:
                hl, radky, crs = z_gml(b)
                crs = crs or "EPSG:4326?"
            if radky:
                vys.append((f"{jm} ({tit})" if tit and tit != jm else jm, hl, radky, crs))
                ziskano = True
                break
        if not ziskano:
            log(f"    wfs vrstva {jm}: nic")
    return vys, "" if vys else "žádná vrstva se nenačetla"


def nacti_zdroj(z):
    """Vrátí seznam tabulek [(popis, sloupce, řádky, crs)] a případnou chybu."""
    url, fmt = z["url"], z["format"]
    low = url.lower()
    if not url.startswith("http"):
        return [], "bez adresy"
    try:
        if "wfs" in fmt or "service=wfs" in low or re.search(r"/wfs\b|wfs\?|_wfs|wfs_", low):
            return wfs(url)
        if "featureserver" in low or "mapserver" in low:
            m = re.match(r"(.*?/(?:FeatureServer|MapServer))(?:/(\d+))?", url, re.I)
            if m:
                vrstvy = [m.group(2)] if m.group(2) else ["0", "1", "2"]
                vys = []
                for v in vrstvy:
                    b, chyba, _ = stahni(f"{m.group(1)}/{v}/query",
                                         {"where": "1=1", "outFields": "*", "f": "geojson", "outSR": "4326",
                                          "resultRecordCount": "20000"})
                    if b and b.lstrip()[:1] == b"{":
                        hl, radky, crs = z_json(b)
                        if radky:
                            vys.append((f"vrstva {v}", hl, radky, "EPSG:4326"))
                return vys, "" if vys else "ArcGIS: nic"
        if re.search(r"/collections/[^/]+(/items)?/?(\?|$)", low):
            zaklad = bez_dotazu(url)
            if not zaklad.rstrip("/").endswith("/items"):
                zaklad = zaklad.rstrip("/") + "/items"
            radky_vse, hl, start = [], [], 0
            while start < MAX_RADKU:
                b, chyba, _ = stahni(zaklad, {"f": "json", "limit": "5000", "offset": str(start)})
                if not b:
                    break
                h, radky, _crs = z_json(b)
                if not radky:
                    break
                hl = hl or h
                radky_vse.extend(radky)
                if len(radky) < 5000:
                    break
                start += 5000
            return ([("OGC API", hl, radky_vse, "EPSG:4326")] if radky_vse else []), "" if radky_vse else "OGC API: nic"
        b, chyba, ct = stahni(url)
        if not b:
            return [], chyba
        hlava = b.lstrip()[:400].lower()
        if b[:4] == b"PK\x03\x04":
            if b"xl/" in b[:4000] or "xlsx" in fmt or low.endswith(".xlsx") or b"[Content_Types].xml" in b[:2000]:
                hl, radky = z_xlsx(b)
                return ([("xlsx", hl, radky, "")] if radky else []), "" if radky else "xlsx: prázdné"
            return [], "zip (přeskočeno)"
        if b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
            hl, radky = z_xls(b)
            return ([("xls", hl, radky, "")] if radky else []), "" if radky else "xls: prázdné"
        if hlava[:1] in (b"{", b"["):
            hl, radky, crs = z_json(b)
            return ([("json", hl, radky, crs)] if radky else []), "" if radky else "json: žádný seznam"
        if hlava.startswith(b"<!doctype html") or hlava.startswith(b"<html") or b"<html" in hlava:
            return [], "html stránka"
        if hlava[:1] == b"<":
            if b"wfs_capabilities" in hlava or b"wfs:wfs_capabilities" in b[:3000].lower():
                return wfs(url)
            hl, radky, crs = z_gml(b)
            return ([("xml", hl, radky, crs)] if radky else []), "" if radky else "xml: nic"
        if b[:4] == b"%PDF":
            return [], "pdf"
        hl, radky = z_csv(b)
        return ([("csv", hl, radky, "")] if radky else []), "" if radky else "csv: prázdné"
    except Exception as e:  # noqa: BLE001
        return [], f"chyba: {type(e).__name__} {str(e)[:120]}"


R_SL_NAZEV = re.compile(r"name|bezeichnung|einrichtung|kita|titel|title|objekt|traeger|träger", re.I)
R_SL_MISTO = re.compile(r"str|adress|anschrift|plz|postleit|ort\b|ort_|_ort|gemeinde|stadtteil|^_x$|lat|geo|hausn", re.I)


def je_seznam_zarizeni(hl, radky):
    if len(radky) < 5:
        return False
    if not any(R_SL_NAZEV.search(h) for h in hl):
        return False
    if not any(R_SL_MISTO.search(h) for h in hl):
        return False
    # aspoň polovina řádků má nějaký delší text (název) – statistiky mají skoro jen čísla
    textove = 0
    for r in radky[:200]:
        if any(len(v) >= 6 and not re.fullmatch(r"[\d.,\s%-]+", v) for v in r.values()):
            textove += 1
    return textove >= min(len(radky), 200) * 0.5


def main():
    os.makedirs(RAW, exist_ok=True)
    sady, hotove_katalogy = {}, set()
    for jmeno, api in CKAN:
        if jmeno in hotove_katalogy:
            continue
        nal, ok = hledej_ckan(jmeno, api)
        if ok:
            hotove_katalogy.add(jmeno)
        for pid, p in nal.items():
            info = sada_info(jmeno, p)
            vse = f"{info['titul']} {info['slug']}"
            if not R_KITA.search(vse) and not R_KITA.search(" ".join(z["nazev"] for z in info["zdroje"])):
                continue
            # stejná sada bývá v GovData i v regionálním katalogu
            klic = re.sub(r"\W+", "", info["titul"].lower()) + "|" + re.sub(r"\W+", "", info["vydavatel"].lower())[:30]
            if klic in sady and sady[klic]["zdroj"] != "govdata":
                continue
            sady[klic] = info
    log(f"\nsad o Kitách celkem: {len(sady)}")

    ukoly, videne_url = [], set()
    for klic, s in sady.items():
        s["otevrena"] = bool(OTEVRENE.search(f"{s['licence']} {s['licenceUrl']}"))
        s["statistika"] = bool(R_STATISTIKA.search(s["titul"]))
        for i, z in enumerate(s["zdroje"]):
            z["stav"] = ""
            f = z["format"]
            pripona = os.path.splitext(urlparse(z["url"]).path)[1].lower().lstrip(".")
            f = f.rstrip("/").rsplit("/", 1)[-1].replace("_srvc", "")
            z["format"] = f
            vhodny = (f in FORMATY or any(k in f for k in ("csv", "xls", "json", "wfs", "gml", "ogc", "arcgis", "esri"))
                      or pripona in ("csv", "xlsx", "xls", "json", "geojson", "tsv", "txt", "xml", "gml")
                      or re.search(r"wfs|featureserver|/collections/|geojson|outputformat", z["url"], re.I))
            if not vhodny:
                z["stav"] = "formát"
            elif z["url"] in videne_url:
                z["stav"] = "duplicitní adresa"
            elif re.search(r"wms\b|service=wms|wmts|\.pdf$|\.zip$|\.kml$|\.kmz$|\.shp$", z["url"], re.I) and "wfs" not in z["url"].lower():
                z["stav"] = "formát"
            else:
                videne_url.add(z["url"])
                ukoly.append((klic, i))
    log(f"zdrojů ke stažení: {len(ukoly)}")

    vysledky = {}

    def prace(u):
        klic, i = u
        z = sady[klic]["zdroje"][i]
        return u, nacti_zdroj(z)

    with ThreadPoolExecutor(8) as ex:
        fut = [ex.submit(prace, u) for u in ukoly]
        for n, f in enumerate(as_completed(fut), 1):
            try:
                u, (tabulky, chyba) = f.result()
            except Exception as e:  # noqa: BLE001
                continue
            vysledky[u] = (tabulky, chyba)
            if n % 50 == 0:
                log(f"  staženo {n}/{len(ukoly)}")

    utraceno, ulozeno = 0, 0
    # nejdřív otevřené licence a větší tabulky
    poradi = sorted(vysledky, key=lambda u: (not sady[u[0]]["otevrena"], -max([len(t[2]) for t in vysledky[u][0]] or [0])))
    for u in poradi:
        klic, i = u
        s, z = sady[klic], sady[klic]["zdroje"][i]
        tabulky, chyba = vysledky[u]
        z["tabulky"] = []
        if not tabulky:
            z["stav"] = chyba or "nic"
            continue
        for popis, hl, radky, crs in tabulky:
            info = {"popis": popis, "radku": len(radky), "sloupce": hl[:60], "crs": crs,
                    "ukazka": radky[:2], "seznam": je_seznam_zarizeni(hl, radky)}
            if info["seznam"] and utraceno < ROZPOCET:
                rid = hashlib.sha1(f"{z['url']}|{popis}".encode()).hexdigest()[:12]
                data = json.dumps({"meta": {k: s[k] for k in ("zdroj", "id", "slug", "titul", "vydavatel", "autor",
                                                               "licence", "licenceUrl", "web", "upraveno", "otevrena")}
                                   | {"url": z["url"], "format": z["format"], "zdrojNazev": z["nazev"],
                                      "zdrojLicence": z["licence"], "popis": popis, "crs": crs},
                                   "sloupce": hl, "radky": radky}, ensure_ascii=False, separators=(",", ":")).encode()
                gz = gzip.compress(data, 6)
                with open(os.path.join(RAW, f"{rid}.json.gz"), "wb") as fh:
                    fh.write(gz)
                utraceno += len(gz)
                ulozeno += 1
                info["soubor"] = f"{rid}.json.gz"
            z["tabulky"].append(info)
        z["stav"] = "ok"

    json.dump({"datum": time.strftime("%Y-%m-%d"), "katalogy": sorted(hotove_katalogy),
               "sady": sorted(sady.values(), key=lambda s: (s["zdroj"], s["titul"]))},
              open(os.path.join(OUT, "katalog.json"), "w"), ensure_ascii=False, indent=1)
    log(f"\nuloženo {ulozeno} seznamů zařízení, {utraceno / 1e6:.1f} MB")


if __name__ == "__main__":
    main()

"""Fotky Kit z Wikimedia Commons (volné licence) – pro Kita Finder.

Zdroje:
  1) OSM tagy wikimedia_commons / image (File:…) přímo u Kity,
  2) soubory v kategorii „Kindergartens in Germany“ (a podkategoriích) se souřadnicemi,
     které leží do 60 m od Kity.
Bereme jen licence CC0, Public domain, CC BY, CC BY-SA. Fotky ukládáme zmenšené
(de/fotky/<osm id>/<n>.webp, 1280 px a náhled 640 px) a metadata s autorem a licencí
do de/data/fotky.json – web je u fotky uvádí.
"""
import io, json, math, os, re, shutil, time
from collections import defaultdict
import requests
from PIL import Image

OUT = os.path.join(os.path.dirname(__file__), "..")
API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "kita-finder-data/1.0 (https://github.com/ondrejpsenicka26/rodicovnik-fotky; contact via repo)"}
KOREN = ["Category:Kindergartens in Germany", "Category:Kindergartens in Germany by state", "Category:Daycare centers in Germany",
         "Category:Day care centres in Germany", "Category:Child care in Germany", "Category:Nursery schools in Germany",
         "Category:Kindertagesstätten in Deutschland", "Category:Preschools in Germany"]
# v názvu souboru musí být slovo, které ukazuje na Kitu (jinak jde často o vedlejší dům nebo ulici)
KLIC = re.compile(r"\bkita\b|\bkitas\b|kindergarten|kindergärten|kindertages|kinderkrippe|\bkrippe\b|kinderhaus|"
                  r"kinderladen|\bkiga\b|kinderhort|familienzentrum|kindertagesheim|kinder[- ]garten", re.I)
# názvy, které Kitu jmenují, ale fotka ukazuje něco jiného (pomníky, zastávky, letecké snímky …)
NE = re.compile(r"stolperstein|erinnerungszeichen|gedenk|luftbild|aerial|panorama|wegweiser|elterntaxi|grab|"
                r"friedhof|haltestelle|bushalt|straßenschild|schild", re.I)
OBECNA = {"kita", "kindergarten", "kindertagesstätte", "kindertagesstaette", "kinderkrippe", "krippe", "haus", "kinder",
          "evangelische", "evangelischer", "katholische", "katholischer", "städtische", "staedtische", "städtischer",
          "kindertageseinrichtung", "familienzentrum", "integrative", "gemeinde", "sankt", "st.", "der", "die", "das",
          "und", "für", "fuer", "am", "an", "im", "in", "zum", "zur", "kath.", "ev.", "kiga", "awo", "drk", "johanniter",
          "diakonie", "caritas", "kinderhaus", "kinderland", "tagesstätte", "waldkindergarten", "e.v.", "gmbh"}
MAX_FOTEK_CELKEM = 2500
POVOLENE = re.compile(r"^(cc0|public domain|pd|cc by(-sa)? ?\d|cc-by(-sa)?-\d)", re.I)
VYLOUCIT = re.compile(r"kinder (spielen|beim|im)|children|kids|fest|feier|einweihung|richtfest|grundstein|spatenstich|"
                      r"interior|innen|logo|plan|karte|map|schild|sign", re.I)
MAX_NA_KITU = 2
s = requests.Session()
s.headers.update(UA)


def api(**p):
    p.update(format="json", formatversion="2")
    for pokus in range(5):
        try:
            r = s.get(API, params=p, timeout=60)
            if r.status_code == 200:
                return r.json()
        except Exception as e:
            print("api", e, flush=True)
        time.sleep(3 * (pokus + 1))
    return {}


def kategorie_strom():
    videno, fronta, soubory = set(), list(KOREN), set()
    while fronta and len(videno) < 4000:
        cat = fronta.pop(0)
        if cat in videno:
            continue
        videno.add(cat)
        cont = {}
        while True:
            j = api(action="query", list="categorymembers", cmtitle=cat, cmtype="subcat|file", cmlimit="500", **cont)
            for m in j.get("query", {}).get("categorymembers", []):
                if m["ns"] == 14:
                    fronta.append(m["title"])
                elif m["ns"] == 6:
                    soubory.add(m["title"])
            if "continue" not in j:
                break
            cont = j["continue"]
        time.sleep(0.1)
    print("kategorií", len(videno), "souborů", len(soubory), flush=True)
    return soubory


def geohledani(kity):
    """Soubory na Commons do 80 m od každé Kity, jejichž název ukazuje na Kitu."""
    from concurrent.futures import ThreadPoolExecutor
    nalez = defaultdict(set)

    def jedna(k):
        j = api(action="query", list="geosearch", gscoord=f"{k['lat']}|{k['lon']}", gsradius="80", gsnamespace="6",
                gslimit="50")
        out = []
        tokeny = {t for t in re.findall(r"[\wäöüß]{5,}", (k["t"].get("name") or "").lower()) if t not in OBECNA}
        for g in j.get("query", {}).get("geosearch", []):
            titul = g["title"]
            tl = titul.lower()
            # jen názvy, které Kitu jasně jmenují (shoda slov z názvu Kity přinášela kostely a pomníky)
            if KLIC.search(titul) and not NE.search(titul):
                out.append((titul, g.get("dist", 0)))
        return k["id"], out

    # průběžná mezipaměť: další běh pokračuje tam, kde předchozí skončil
    cache_cesta = os.path.join(OUT, "data", "commons-geo.json")
    try:
        cache = json.load(open(cache_cesta))
    except Exception:  # noqa: BLE001
        cache = {}
    zbyva = [k for k in kity if k["id"] not in cache]
    print("geohledání: v mezipaměti", len(cache), "zbývá", len(zbyva), flush=True)
    start = time.time()
    with ThreadPoolExecutor(4) as ex:
        for i, (kid, out) in enumerate(ex.map(jedna, zbyva)):
            cache[kid] = [t for t, _ in out]
            if i % 1000 == 0:
                print("geo", i, "/", len(zbyva), f"{time.time() - start:.0f}s", flush=True)
                json.dump(cache, open(cache_cesta, "w"), ensure_ascii=False, separators=(",", ":"))
            # nejvýš ~3 hodiny geohledání v jednom běhu, zbytek příště
            if time.time() - start > 3 * 3600:
                print("časový limit geohledání – pokračuje příští běh", flush=True)
                break
    json.dump(cache, open(cache_cesta, "w"), ensure_ascii=False, separators=(",", ":"))
    for kid, tituly in cache.items():
        for t in tituly:
            nalez[kid].add(t)
    return nalez


def info(tituly):
    out = {}
    tituly = list(tituly)
    for i in range(0, len(tituly), 50):
        j = api(action="query", titles="|".join(tituly[i:i + 50]), prop="coordinates|imageinfo",
                iiprop="url|size|extmetadata|mime", iiurlwidth="1024", colimit="max")
        for p in j.get("query", {}).get("pages", []):
            ii = (p.get("imageinfo") or [{}])[0]
            meta = ii.get("extmetadata", {})
            lic = (meta.get("LicenseShortName", {}) or {}).get("value", "")
            autor = re.sub(r"<[^>]+>", "", (meta.get("Artist", {}) or {}).get("value", "")).strip()
            co = (p.get("coordinates") or [None])[0]
            out[p["title"]] = {
                "lat": co["lat"] if co else None, "lon": co["lon"] if co else None,
                "lic": lic, "licUrl": (meta.get("LicenseUrl", {}) or {}).get("value", ""),
                "autor": autor[:120], "thumb": ii.get("thumburl"), "w": ii.get("thumbwidth"), "h": ii.get("thumbheight"),
                "page": ii.get("descriptionurl"), "mime": ii.get("mime", ""),
            }
        time.sleep(0.2)
    return out


def ulozit(url, cil):
    r = s.get(url, timeout=60)
    r.raise_for_status()
    im = Image.open(io.BytesIO(r.content)).convert("RGB")
    im.thumbnail((1024, 1024))
    os.makedirs(os.path.dirname(cil), exist_ok=True)
    im.save(cil, "WEBP", quality=70, method=6)
    nahled = im.copy()
    nahled.thumbnail((560, 560))
    nahled.save(cil.replace(".webp", "-nahled.webp"), "WEBP", quality=68, method=6)
    barva = im.resize((1, 1)).getpixel((0, 0))
    return im.size, "#%02x%02x%02x" % barva


def main():
    kity = json.load(open(os.path.join(OUT, "data", "kitas-osm.json")))
    # 1) přímé tagy
    prime = defaultdict(list)
    for k in kity:
        t = k["t"]
        for klic in ("wikimedia_commons", "image"):
            v = (t.get(klic) or "").strip()
            m = re.search(r"(File:[^|;]+\.(jpe?g|png|webp|tiff?))", v, re.I)
            if m:
                prime[m.group(1).replace("_", " ")].append(k["id"])
    print("přímé tagy:", len(prime), flush=True)
    # 2) kategorie
    soubory = kategorie_strom() | set(prime)
    # 3) geohledání u každé Kity
    geo = geohledani(kity)
    print("geohledání: Kit", len(geo), "souborů", sum(len(v) for v in geo.values()), flush=True)
    for kid, tituly in geo.items():
        for t in tituly:
            soubory.add(t)
    meta = info(soubory)
    # mřížka Kit pro hledání nejbližší
    mrizka = defaultdict(list)
    for k in kity:
        mrizka[(round(k["lat"], 2), round(k["lon"], 2))].append(k)

    def nejblizsi(lat, lon):
        best, bd = None, 1e9
        for dx in (-0.01, 0, 0.01):
            for dy in (-0.01, 0, 0.01):
                for k in mrizka.get((round(lat + dx, 2), round(lon + dy, 2)), []):
                    d = math.hypot((k["lat"] - lat) * 111000, (k["lon"] - lon) * 111000 * math.cos(math.radians(lat)))
                    if d < bd:
                        best, bd = k, d
        return best, bd

    geo_podle_souboru = defaultdict(list)
    for kid, tituly in geo.items():
        for t in tituly:
            geo_podle_souboru[t].append(kid)
    prirazeni = defaultdict(list)
    for titul, m in meta.items():
        if not m.get("thumb") or not POVOLENE.search(m["lic"] or "") or VYLOUCIT.search(titul):
            continue
        if m["mime"] not in ("image/jpeg", "image/png", "image/webp", "image/tiff"):
            continue
        cile = list(prime.get(titul, []))
        cile += [kid for kid in geo_podle_souboru.get(titul, []) if kid not in cile]
        if not cile and m["lat"] is not None:
            k, d = nejblizsi(m["lat"], m["lon"])
            if k and d <= 60:
                cile = [k["id"]]
        if len(cile or []) > 2:
            cile = cile[:1]
        for c in cile or []:
            prirazeni[c].append((titul, m))
    print("Kit s fotkou:", len(prirazeni), flush=True)

    cil_dir = os.path.join(OUT, "fotky")
    nova_dir = os.path.join(OUT, "fotky.nove")
    shutil.rmtree(nova_dir, ignore_errors=True)
    vystup = {}
    # nejdřív Kity s fotkou z přímého tagu, pak ostatní; celkový strop kvůli velikosti repozitáře
    poradi = sorted(prirazeni.items(), key=lambda kv: (0 if any(t in prime for t, _ in kv[1]) else 1, kv[0]))
    celkem = 0
    for kid, seznam in poradi:
        if celkem >= MAX_FOTEK_CELKEM:
            break
        polozky = []
        for n, (titul, m) in enumerate(seznam[:MAX_NA_KITU], 1):
            cil = os.path.join(nova_dir, kid, f"{n}.webp")
            try:
                (w, h), barva = ulozit(m["thumb"], cil)
            except Exception as e:
                print("chyba", titul, e, flush=True)
                continue
            polozky.append({"n": n, "w": w, "h": h, "barva": barva, "titul": titul, "zdroj": "osm" if titul in prime else "commons",
                            "autor": m["autor"] or "Wikimedia Commons",
                            "licence": m["lic"], "licenceUrl": m["licUrl"], "url": m["page"]})
            time.sleep(0.3)
        if polozky:
            vystup[kid] = polozky
            celkem += len(polozky)
    if vystup:
        shutil.rmtree(cil_dir, ignore_errors=True)
        os.rename(nova_dir, cil_dir)
        json.dump(vystup, open(os.path.join(OUT, "data", "fotky.json"), "w"), ensure_ascii=False, indent=0)
    print("hotovo:", len(vystup), "Kit,", sum(len(v) for v in vystup.values()), "fotek", flush=True)


if __name__ == "__main__":
    main()

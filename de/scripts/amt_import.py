"""Úřední otevřená data → údaje ke Kitám z OSM (Kita Finder).

Čte de/data/amt/raw/*.json.gz (viz amt_katalog.py), sjednotí sloupce vybraných seznamů
(země, města, okresy – jen sady s otevřenou licencí) a spáruje je s Kitami z OpenStreetMap
podle polohy, adresy a názvu. Doplňují se jen údaje, které úřad sám zveřejnil.

Výstup: de/data/amt.json
  zdroje: [{id, nazev, vydavatel, licence, url}]
  kity:   {osm id: {z: [indexy zdrojů], nazev, ulice, plz, obec, tel, mail, web, traeger, typ, kap, vek, provoz, ...}}
  nespárováno: {zdroj: počet}  – zařízení z úředních seznamů, která v OSM nejsou
a de/data/weby-extra.json {osm id: {url, zdroj}} – weby Kit, které OSM nemá (pro weby.py).
"""
import glob, gzip, json, math, os, re, sys
from collections import Counter, defaultdict

OUT = os.path.join(os.path.dirname(__file__), "..", "data")
RAW = os.path.join(OUT, "amt", "raw")

try:
    from pyproj import Transformer
    _T = {}

    def prevod(epsg, x, y):
        if epsg not in _T:
            _T[epsg] = Transformer.from_crs(epsg, 4326, always_xy=True)
        lon, lat = _T[epsg].transform(x, y)
        return lat, lon
except ImportError:  # bez pyproj se přeskočí sady v UTM
    def prevod(epsg, x, y):
        return None, None

DL_BY = "Datenlizenz Deutschland – Namensnennung – Version 2.0"
DL_ZERO = "Datenlizenz Deutschland – Zero – Version 2.0"
CC_BY = "CC BY 4.0"
CC0 = "CC0 1.0"

# sloupce: n = název, s = ulice (případně s číslem), h = číslo, p = PSČ, o = obec, t = telefon,
# m = e-mail, w = web, tr = zřizovatel, typ = druh zřizovatele, k = místa (součet sloupců),
# lat/lon nebo xy (jeden sloupec „lat,lon“) nebo utm (x, y, EPSG)
ZDROJE = [
    dict(id="nrw", soubor="52546acb6c7d", nazev="Kindertageseinrichtungen in NRW",
         vydavatel="Ministerium für Kinder, Jugend, Familie, Gleichstellung, Flucht und Integration des Landes Nordrhein-Westfalen",
         licence=DL_BY, n="Einrichtung Name", s="Straße", p="PLZ", o="Ort", t="Tel", xy="Geokoordinate",
         k=["U3-Plätze", "Ü3-Plätze"]),
    dict(id="sh", soubor="a2336752b8c8", nazev="Kindertagesstätten (Kita-Datenbank Schleswig-Holstein)",
         vydavatel="Ministerium für Soziales, Jugend, Familie, Senioren, Integration und Gleichstellung des Landes Schleswig-Holstein",
         licence=CC_BY, n="name", s="address.street", h="address.number", p="address.postalcode", o="address.town",
         lat="address.latitude", lon="address.longitude", tr="institution", filtr=("type", r"^KITA$"),
         vekM="careFrom", odMs="openingHoursFrom", doMs="openingHoursTo", rollstuhl="barrierfree",
         integrativ="integrational", koncept="educationalConcept"),
    dict(id="berlin", soubor="2d6d8af3d41e", nazev="Kitas in Berlin",
         vydavatel="Senatsverwaltung für Bildung, Jugend und Familie Berlin", licence="CC BY 3.0 DE",
         n="Einrichtungsname", s="Straße", h="Hausnummer", p="PLZ", t="Telefon", predvolba="030",
         tr="Trägername", k=["Erlaubte Plätze (BE)"], utm=("ETRS_XKOORDINATE", "ETRS_YKOORDINATE", 25833), obec="Berlin"),
    dict(id="brandenburg", soubor="d929a3f62862", nazev="Kindertagesstätten im Land Brandenburg",
         vydavatel="Ministerium für Bildung, Jugend und Sport des Landes Brandenburg (MBJS)", licence=DL_BY,
         n="name", s="strasse", h="hausnr", h2="hausnrerw", p="plz", o="ort", k=["kapazitaet"], vekText="altersstruktur",
         filtrNe=("einrichtungsart", r"^Kinderhort|Ganztagsangebote")),
    dict(id="hamburg", soubor="5ec39db78052", nazev="Kita Einrichtungen Hamburg",
         vydavatel="Freie und Hansestadt Hamburg, Behörde für Schule, Familie und Berufsbildung", licence=DL_BY,
         n="Name", s="Strasse", h="Hausnr", p="PLZ", o="Ort", t="Telefon", predvolba="040"),
    dict(id="muenchen", soubor="e23c3a0147b3", nazev="Kindertagesbetreuungseinrichtungen in München",
         vydavatel="Landeshauptstadt München", licence=DL_BY, n="Name Einrichtung", s="Straße", h="Haus-nr.",
         h2="Hausnummern-zusatz", p="PLZ", o="Ort", lat="Geokoordinaten Breite", lon="Geokoordinaten Länge",
         typ="Trägerschaft", filtrNe=("Einrichtungsart", r"^HORT$|^TAGESHEIM$")),
    dict(id="thueringen", soubor="bb39fdb69436", nazev="Kindertagesstätten in Thüringen (kommunaler WFS, Geoproxy Thüringen)",
         vydavatel="Freistaat Thüringen / Thüringer Kommunen (Geoportal Thüringen)", licence=DL_BY, overit=True,
         n="Name", s="Strasse", h="Hausnummer", p="PLZ", o="Ort", t="Telefonnummer", m="EMail", w="Webseite",
         tr="Traeger", k=["Anzahl_der_Plaetze"], vekText="Altersgruppe"),
    dict(id="rvr", soubor="c7ab8e447ce2", nazev="POI – Kindertagesstätten (Metropole Ruhr)", vydavatel="Regionalverband Ruhr",
         licence=DL_BY, n="name", s="strasse", h="hausnummer", p="plz", o="ort", w="link",
         filtr=("kategorie", r"Kindertagesst|Kindergärten|Kindergarten|Kita")),
    dict(id="koeln", soubor="441aabe1ef61", nazev="Kindertagesstätten in Köln", vydavatel="Stadt Köln", licence=DL_ZERO,
         n="postzustellbezirk", s="adresse", typ="traeger_typ", obec="Köln"),
    dict(id="dortmund", soubor="7c9a144ce453", nazev="Kindergärten in Dortmund", vydavatel="Stadt Dortmund", licence=DL_ZERO,
         n="Objektname", s="Straße", h="Hausnummer", o="Ort", xy="Geografische Koordinate"),
    dict(id="leipzig", soubor="9a40c8401146", nazev="Kindertageseinrichtungen, Stadt Leipzig",
         vydavatel="Stadt Leipzig, Amt für Jugend und Familie", licence=DL_BY, n="name_einr", s="strname", h="hnr_alles",
         p="plz", o="ort", tr="traeg_name", t="ap_tel", m="ap_email", k=["gesamtkap"], bezNazvu=True,
         filtrNe=("art_einr", r"^Hort")),
    dict(id="augsburg", soubor="84a7806ff4dd", nazev="Kindertageseinrichtungen in Augsburg",
         vydavatel="Stadt Augsburg, Amt für Kindertagesbetreuung", licence=CC0, n="Kitaname", s="Strasse", h="Hausnr",
         p="PLZ", o="Ort", t="Telefon", m="eMail", w="Homepage", tr="Träger", od="Öffnungszeit von", do="Öffnungszeit bis",
         integrativ="Integrative Einrichtung"),
    dict(id="chemnitz", soubor="6d507a532bbb", nazev="Kindertageseinrichtungen in Chemnitz", vydavatel="Stadt Chemnitz",
         licence=DL_BY, n="BEZEICHNUNG", s="STRASSE", h="HAUSBEZ", p="PLZ", o="ORT", t="TELEFON", m="EMAIL", tr="TRAEGER",
         filtr=("KITA", r"^1"), bezNazvu=True, rollstuhl="BARRIEREFREI", integrativ="INTEGRATIV"),
    dict(id="rostock", soubor="2a5441389216", nazev="Kinderbetreuungseinrichtungen in Rostock",
         vydavatel="Hanse- und Universitätsstadt Rostock", licence=CC0, n="bezeichnung", s="strasse_name", h="hausnummer",
         h2="hausnummer_zusatz", p="postleitzahl", o="gemeinde_name", lat="latitude", lon="longitude", t="telefon",
         m="email", w="website", tr="traeger_bezeichnung", k=["plaetze"], auto=True),
    dict(id="aachen-stadt", soubor="f25c63683e8a", nazev="Kindertagesstätten Stadt Aachen", vydavatel="Stadt Aachen",
         licence=DL_BY, n="name", s="strasse", p="plz", o="ort", t="telefon", k=["u3plaetze", "ue3plaetze"]),
    dict(id="aachen-region", soubor="aa6b208a87ec", nazev="Tageseinrichtungen für Kinder in der StädteRegion Aachen",
         vydavatel="StädteRegion Aachen", licence=DL_BY, n="Einrichtung_Name", s="Anschrift", p="PLZ", o="Ort",
         t="Telefon", m="EMail", w="Kita_Webauftritt", tr="Traeger", typ="Einrichtung_Art", auto=True),
    dict(id="niederrhein", soubor="cc35bbf833e3", nazev="Kindergärten und Kindertagesstätten (Kreise Kleve, Viersen, Wesel, Stadt Krefeld)",
         vydavatel="Kreis Viersen / Kommunales Rechenzentrum Niederrhein", licence=DL_ZERO, n="kitaname", s="strasse",
         h="hnr", p="plz", o="kommune", t="telefon", m="email", w="homepage", tr="traeger", k=["anzahl_plaetze"],
         koncept="paedagogische_schwerpunkte", auto=True),
    dict(id="recklinghausen", soubor="cdadb8295d2a", nazev="Kreis Recklinghausen: Kindergärten",
         vydavatel="Kreis Recklinghausen", licence=DL_BY, overit=True, n="bezeichnung", s="strasse_hsnr", p="plz",
         o="ort", t="telefon", m="email", w="internet", tr="traeger", k=["u3plaetze", "ue3plaetze"]),
    dict(id="gelsenkirchen", soubor="acac4eb437a2", nazev="Kitas der Stadt Gelsenkirchen", vydavatel="Stadt Gelsenkirchen",
         licence=DL_ZERO, n="Name", s="Strasse", p="PLZ", t="Telefon", m="EMail", w="INTERNET", obec="Gelsenkirchen",
         bezNazvu=True),
    dict(id="krefeld", soubor="225af7abe1f8", nazev="Geodaten Kindergärten Stadt Krefeld", vydavatel="Stadt Krefeld",
         licence=DL_ZERO, n="KITANAME", s="STRASSE", h="HNR", p="PLZ", o="KOMMUNE", m="EMAIL", t="TELEFON", w="HOMEPAGE",
         tr="TRAEGER", k=["ANZAHL_PLAETZE"], auto=True),
    dict(id="hamm", soubor="31746f210f30", nazev="Kindergärten und -tagesstätten in der Stadt Hamm", vydavatel="Stadt Hamm",
         licence=DL_BY, n="kitaname", s="adresse", p="plz", o="ort", m="email", w="homepage", t="telefon",
         lat="lage_breitengrad", lon="lage_laengengrad", od="oeffnungszeit_von", do="oeffnungszeit_bis",
         tr="traeger", integrativ="integrative_einrichtung", rollstuhl="barrierefreiheit", auto=True),
    dict(id="bottrop", soubor="8c4994d34547", nazev="Tageseinrichtungen für Kinder Bottrop", vydavatel="Stadt Bottrop",
         licence=DL_ZERO, n="Name", s="Strasse", h="HsNr", p="PLZ", o="Ort", t="TELEFON", k=["AnzPlaetze"]),
    dict(id="dormagen", soubor="c4b383dbec0e", nazev="Dormagen: Kindertagesbetreuung", vydavatel="Stadt Dormagen",
         licence=DL_ZERO, n="Name", s="Strasse", h="Hausnummer", p="PLZ", o="Ort", t="Telefon", m="Mail", tr="Träger",
         xy="geopunkt", filtr=("Typ", r"Kindertageseinrichtung|Kita")),
    dict(id="minden", soubor="eacb4f532e97", nazev="Kindertageseinrichtungen Stadt Minden", vydavatel="Stadt Minden",
         licence=DL_ZERO, n="kibiz_bezeichnung", s="strasse", h="hausnummer", p="plz", o="ort", t="telefonnummer",
         m="email", w="homepage", tr="traeger", k=["u3plaetze", "ue3plaetze"], auto=True),
    dict(id="grevenbroich", soubor="78debf81c21f", nazev="Grevenbroich: Kindertageseinrichtungen", vydavatel="Stadt Grevenbroich",
         licence=CC0, n="Name der Einrichtung", s="Straße", p="PLZ", o="Stadt", tr="Träger", k=["Plätze"], xy="geokoordinaten"),
    dict(id="kleve", soubor="99a04cedf22b", nazev="Kindertagesstätten in Kleve", vydavatel="Stadt Kleve", licence=DL_ZERO,
         n="KITANAME", s="STRASSE", h="HAUSNUMMER", p="PLZ", o="ORT", t="TELEFON1", m="EMAIL", k=["PLAETZE"]),
    dict(id="ludwigslust-parchim", soubor="755f6837f70c", nazev="Kindertagesstätten im Landkreis Ludwigslust-Parchim",
         vydavatel="Landkreis Ludwigslust-Parchim", licence=DL_BY, n="Name", s="Strasse", po="Ort", t="Tel", m="Mail",
         tr="Traeger"),
    dict(id="erkrath", soubor="73c4b2ec9d2e", nazev="Kindertagesstätten Erkrath", vydavatel="Stadt Erkrath", licence=DL_ZERO,
         n="KITANAME", s="STRASSE_HAUSNR", po="PLZ_ORT", t="TELEFON", m="E_MAIL"),
    dict(id="elmshorn", soubor="e41ee9110f27", nazev="Kinderbetreuung im Bereich der Stadt Elmshorn", vydavatel="Stadt Elmshorn",
         licence=CC_BY, n="EINRICHTUNG", s="STRASSE_TEXT", h="HNR", p="PLZ", o="ORT", t="TELEFON", m="EMAIL", w="WEBSITE",
         utm=("_x", "_y", 25832)),
    dict(id="norderstedt", soubor="bde0c22dcfd1", nazev="Kindertagesstätten Norderstedt", vydavatel="Stadt Norderstedt",
         licence=DL_BY, n="bezeich", s="lage", po="ort", tr="traeger", w="homepage"),
    dict(id="dresden", soubor="a7143f563976", nazev="Kindertagesstätten in Dresden", vydavatel="Landeshauptstadt Dresden",
         licence=DL_BY, n="name", nazevBez=r"\s+-\s+[^-]*\d+\s*[a-z]?$", s="strasse", h="hausnummer", p="plz", o="ort",
         t="fon", m="e_mail", w="url", tr="traeger", k=["plaetze_kita", "plaetze_krippe"], vekM="aufnahmealter",
         filtrNe=("einrichtungsart", r"Auslagerung")),
    dict(id="goettingen", soubor="bf85e901971a", nazev="Kindertagesstätten Göttingen", vydavatel="Stadt Göttingen",
         licence=CC0, n="NAME", s="ADRESSE", obec="Göttingen", w="WEB", tr="ORGANISATI",
         filtrNe=("JUNIORCLUB", r"^ja$")),
]

R_PORTAL = re.compile(r"kita-navigator|kita-online|kitaportal|kita-portal|little-bird|geoportal|geoservice|"
                      r"kitaplaner|kita-planer|/kita/details/|webkita|kivan|kitafinder|kita-finder|meinkitaplatz", re.I)
STOP = set("kita kitas kindertagesstaette kindertagesstaetten kindertageseinrichtung kindergarten kiga kinderkrippe krippe "
           "kinderhaus tageseinrichtung fuer kinder staedtische staedtischer staedt staedtisches ev evang evangelische "
           "evangelischer evangelisches kath katholische katholischer katholisches familienzentrum fz der die das und e v "
           "tagesstaette kindertagesstatte integrative integrativer kommunale kommunaler haus am an im in zum zur des "
           "tek kth kindertages einrichtung awo drk".split())


def bez_dia(s):
    return (s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
            .replace("é", "e").replace("è", "e"))


def tokeny(nazev):
    return {t for t in re.findall(r"[a-z0-9]+", bez_dia(nazev or "")) if t not in STOP and len(t) >= 2}


def shoda_nazvu(a, b):
    """0–1, nebo None, když jeden z názvů nemá žádné rozlišující slovo."""
    ta, tb = tokeny(a), tokeny(b)
    if not ta or not tb:
        return None
    spol = ta & tb
    if not spol:
        # „Sonnenschein“ vs „Kita-Sonnenschein e.V.“ řeší tokeny; tady ještě slepená slova
        ja, jb = "".join(sorted(ta)), "".join(sorted(tb))
        return 0.6 if (len(ja) >= 6 and ja in jb) or (len(jb) >= 6 and jb in ja) else 0.0
    return len(spol) / min(len(ta), len(tb)) if any(len(t) >= 4 for t in spol) else 0.3


def norm_ulice(s):
    s = bez_dia(s or "")
    s = re.sub(r"strasse|str\.|str\b", "str", s)
    return re.sub(r"[^a-z0-9]", "", s)


def rozdel_ulici(s):
    """„Am Markt 12 a“ → („Am Markt“, „12a“)"""
    s = re.sub(r"\s+", " ", (s or "").strip())
    m = re.match(r"^(.*?\D)\s*(\d+\s?[a-zA-Z]?(?:\s?[-/]\s?\d+\s?[a-zA-Z]?)?)$", s)
    if m and len(m.group(1).strip()) >= 3:
        return m.group(1).strip(" ,"), re.sub(r"\s+", "", m.group(2)).lower()
    return s, ""


def cislo(x):
    try:
        return float(str(x).replace(",", ".").strip())
    except (ValueError, TypeError):
        return None


def telefon(t, predvolba=""):
    t = re.sub(r"\s+", " ", (t or "").strip())
    if not t or not re.search(r"\d{4}", re.sub(r"\D", "", t)) or re.search(r"nicht|keine|unbekannt", t, re.I):
        return ""
    t = t.split(";")[0].split(",")[0].strip()
    cisl = re.sub(r"\D", "", t)
    if predvolba and not t.startswith(("0", "+", "(0")):
        t = f"{predvolba} {t}"
    elif not t.startswith(("0", "+", "(0")):
        return ""
    return t[:30] if 6 <= len(cisl) <= 15 else ""


def web(u):
    u = (u or "").strip()
    m = re.search(r'href="?([^">\s]+)', u, re.I)
    if m:
        u = m.group(1)
    u = u.split()[0] if u else ""
    if not u or "." not in u or "@" in u or R_PORTAL.search(u):
        return ""
    if not u.startswith("http"):
        u = "https://" + u
    return u[:200]


def mail(m):
    m = (m or "").strip().split(";")[0].split(",")[0].strip().lower()
    return m if re.fullmatch(r"[^\s@<>()\",;:]+@[^\s@<>()\",;:]+\.[a-z]{2,}", m) and len(m) <= 80 else ""


def ano(x):
    return str(x).strip().lower() in ("1", "true", "ja", "t", "yes", "j", "x")


def cas(x):
    m = re.match(r"^(\d{1,2})[:.](\d{2})", str(x or "").strip())
    return (int(m.group(1)) * 60 + int(m.group(2))) if m else None


def vek_text(t):
    """„1 Jahr - Schuleintritt“, „0 Jahre bis Schuleintritt“, „ab 8 Wochen“ → měsíce"""
    t = (t or "").lower()
    m = re.search(r"(\d+)\s*(woche|monat|jahr)", t)
    if not m:
        return None
    n = int(m.group(1))
    v = n * 12 if m.group(2) == "jahr" else n if m.group(2) == "monat" else max(1, round(n / 4.3))
    return v if 0 <= v <= 72 else None


def auto_sloupec(radek, vzory):
    for k in radek:
        kk = re.sub(r"[^a-z0-9]", "", bez_dia(k))
        if any(re.fullmatch(v, kk) for v in vzory):
            return k
    return None


def poloha(r, z, crs):
    lat = lon = None
    if z.get("xy") and r.get(z["xy"]):
        c = re.split(r"[,;\s]+", r[z["xy"]].strip())
        if len(c) >= 2:
            lat, lon = cislo(c[0]), cislo(c[1])
    elif z.get("lat") and r.get(z["lat"]):
        lat, lon = cislo(r.get(z["lat"])), cislo(r.get(z["lon"]))
    elif z.get("utm") and r.get(z["utm"][0]):
        x, y = cislo(r[z["utm"][0]]), cislo(r[z["utm"][1]])
        if x and y:
            if x > 30_000_000:  # číslo zóny před souřadnicí
                x -= 32_000_000 if x < 33_000_000 else 33_000_000
            lat, lon = prevod(z["utm"][2], x, y)
    elif r.get("_x"):
        x, y = cislo(r["_x"]), cislo(r["_y"])
        if x is not None and y is not None:
            if abs(x) <= 180 and abs(y) <= 180:
                lat, lon = y, x
            else:
                m = re.search(r"(3857|900913|258\d\d|3146\d|4647|5650)", crs or "")
                epsg = int(m.group(1)) if m else 25832
                if epsg in (4647, 5650):
                    x -= 32_000_000 if epsg == 4647 else 33_000_000
                    epsg = 25832 if epsg == 4647 else 25833
                lat, lon = prevod(epsg, x, y)
    if lat is None or lon is None:
        return None, None
    if not (46.5 <= lat <= 55.5) and (46.5 <= lon <= 55.5):
        lat, lon = lon, lat
    if not (46.5 <= lat <= 55.5 and 5.3 <= lon <= 15.6):
        return None, None
    return lat, lon


def nacti_zdroj(z):
    cesta = os.path.join(RAW, f"{z['soubor']}.json.gz")
    if not os.path.exists(cesta):
        print("  chybí soubor", z["id"], file=sys.stderr)
        return []
    d = json.load(gzip.open(cesta))
    crs = d["meta"].get("crs", "")
    z["url"] = d["meta"].get("web") or d["meta"].get("url", "")
    z["datasetUrl"] = d["meta"].get("url", "")
    out = []
    for r in d["radky"]:
        if z.get("filtr") and not re.search(z["filtr"][1], r.get(z["filtr"][0], ""), re.I):
            continue
        if z.get("filtrNe") and re.search(z["filtrNe"][1], r.get(z["filtrNe"][0], ""), re.I):
            continue
        g = lambda k: (r.get(z[k]) or "").strip() if z.get(k) else ""  # noqa: E731
        if z.get("auto"):
            # sloupce se v některých sadách jmenují trochu jinak, než čekáme
            for klic, vzory in (("t", [r"telefon\d?", r"tel", r"telefonnummer", r"telefonfestnetz"]), ("m", [r"e?mail", r"emailadresse"]),
                                ("w", [r"homepage", r"website", r"webseite", r"internet", r"kitawebauftritt"]),
                                ("tr", [r"traeger", r"traegername", r"traegerbezeichnung"]),
                                ("n", [r"bezeichnung", r"name", r"kitaname", r"einrichtungname"]),
                                ("p", [r"plz", r"postleitzahl"]), ("s", [r"strasse", r"strassename", r"anschrift", r"adresse"]),
                                ("h", [r"hausnummer", r"hnr", r"hausnr"])):
                if z.get(klic) and z[klic] not in r:
                    a = auto_sloupec(r, vzory)
                    if a:
                        z[klic] = a
        nazev = re.sub(r"\s+", " ", g("n").replace('"', "").replace("„", "").replace("“", "")).strip()
        if z.get("nazevBez"):
            nazev = re.sub(z["nazevBez"], "", nazev).strip()
        ulice, cd = g("s"), re.sub(r"\s+", "", g("h") + g("h2")).lower()
        if not cd:
            ulice, cd = rozdel_ulici(ulice)
        plz, obec = g("p")[:5], g("o") or z.get("obec", "")
        if z.get("po") and r.get(z["po"]):
            m = re.match(r"\s*(\d{5})\s+(.+)", r[z["po"]])
            if m:
                plz, obec = m.group(1), m.group(2).strip()
        if plz and not re.fullmatch(r"\d{5}", plz):
            plz = plz.zfill(5) if plz.isdigit() and len(plz) == 4 else ""
        lat, lon = poloha(r, z, crs)
        if not nazev and not ulice:
            continue
        kap = None
        if z.get("k"):
            s = [cislo(r.get(c)) for c in z["k"]]
            s = [x for x in s if x is not None]
            kap = int(sum(s)) if s and 5 <= sum(s) <= 600 else None
        vek = None
        if z.get("vekM") and cislo(r.get(z["vekM"])) is not None:
            v = int(cislo(r[z["vekM"]]))
            vek = v if 0 <= v <= 72 else None
        if z.get("vekText"):
            vek = vek_text(r.get(z["vekText"]))
        provoz = ""
        od = do = None
        if z.get("odMs") and cislo(r.get(z["odMs"])) and cislo(r.get(z["doMs"])):
            od, do = int(cislo(r[z["odMs"]]) / 60000), int(cislo(r[z["doMs"]]) / 60000)
        elif z.get("od"):
            od, do = cas(r.get(z["od"])), cas(r.get(z["do"]))
        if od is not None and do is not None and 5 * 60 <= od <= 10 * 60 and 11 * 60 <= do <= 21 * 60:
            provoz = f"{od // 60}:{od % 60:02d}-{do // 60}:{do % 60:02d}"
        out.append({
            "nazev": "" if z.get("bezNazvu") and re.search(r"\d", nazev[:40]) and not re.search(r"kita|kinder", nazev, re.I) else nazev,
            "nazevPuvodni": nazev, "ulice": ulice, "cd": cd, "plz": plz, "obec": obec, "lat": lat, "lon": lon,
            "tel": telefon(g("t"), z.get("predvolba", "")), "mail": mail(g("m")), "web": web(g("w")),
            "traeger": re.sub(r"\s+", " ", g("tr"))[:120], "typ": g("typ"), "kap": kap, "vek": vek, "provoz": provoz,
            "rollstuhl": ano(r.get(z["rollstuhl"])) if z.get("rollstuhl") else None,
            "integrativ": ano(r.get(z["integrativ"])) if z.get("integrativ") else None,
            "koncept": g("koncept")[:200],
        })
    return out


def vzd(a, b, c, d):
    x = math.radians(d - b) * math.cos(math.radians((a + c) / 2))
    return 6371000 * math.hypot(x, math.radians(c - a))


LADENI = [] if os.environ.get("LADENI") else None
KID = {}


def main():
    kity = json.load(open(os.path.join(OUT, "kitas-osm.json")))
    obce = {}
    try:
        for g in json.load(open(os.path.join(OUT, "gemeinden.json"))):
            obce[g["ags"]] = g["name"]
    except Exception:  # noqa: BLE001
        pass
    mriz = defaultdict(list)
    podle_plz = defaultdict(list)
    for k in kity:
        t = k["t"]
        k["_n"] = t.get("name") or t.get("official_name") or ""
        k["_s"] = norm_ulice(t.get("addr:street", ""))
        k["_h"] = re.sub(r"\s+", "", t.get("addr:housenumber", "")).lower()
        k["_obec"] = bez_dia(obce.get(k.get("ags", ""), "") or t.get("addr:city", ""))
        mriz[(round(k["lat"] * 100), round(k["lon"] * 100))].append(k)
        if t.get("addr:postcode"):
            podle_plz[t["addr:postcode"]].append(k)
    podle_obce = defaultdict(list)
    for k in kity:
        if k["_obec"]:
            podle_obce[k["_obec"]].append(k)

    global KID
    KID = {k["id"]: k for k in kity}
    vysledek, zdroje_out, nesparovano, statistika = {}, [], {}, []
    for zi, z in enumerate(ZDROJE):
        radky = nacti_zdroj(z)
        zdroje_out.append({k: z.get(k) for k in ("id", "nazev", "vydavatel", "licence", "url", "datasetUrl")}
                          | ({"overit": True} if z.get("overit") else {}))
        # kandidátní dvojice se skóre
        dvojice = []
        for ri, r in enumerate(radky):
            kand = []
            if r["lat"] is not None:
                gx, gy = round(r["lat"] * 100), round(r["lon"] * 100)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for k in mriz.get((gx + dx, gy + dy), ()):
                            d = vzd(r["lat"], r["lon"], k["lat"], k["lon"])
                            if d <= 400:
                                kand.append((k, d))
            else:
                videne = set()
                for k in podle_plz.get(r["plz"], []) + podle_obce.get(bez_dia(r["obec"]), []):
                    if k["id"] not in videne:
                        videne.add(k["id"])
                        kand.append((k, None))
            su = norm_ulice(r["ulice"])
            for k, d in kand:
                sn = shoda_nazvu(r["nazevPuvodni"], k["_n"])
                adr = bool(su and k["_s"] and su == k["_s"] and r["cd"] and k["_h"] and r["cd"] == k["_h"])
                ulice_ok = bool(su and k["_s"] and su == k["_s"])
                # stejná ulice, ale jiné číslo domu (13 a 13a je totéž místo, 23 a 35 ne)
                c1, c2 = re.match(r"\d+", r["cd"] or ""), re.match(r"\d+", k["_h"] or "")
                jine_cislo = bool(ulice_ok and c1 and c2 and c1.group() != c2.group())
                skore = 0.0
                if d is not None:
                    if adr and d <= 400:
                        skore = 3 + (sn or 0)
                    elif sn is not None and sn >= 0.6 and d <= 300:
                        skore = 2 + sn - d / 1000
                    elif d <= 60 and (sn is None or sn >= 0.3) and not jine_cislo:
                        skore = 1.5 - d / 200
                    elif d <= 150 and ulice_ok and (sn is None or sn > 0) and not jine_cislo:
                        skore = 1.2 - d / 500
                else:
                    if adr and (sn is None or sn > 0 or True):
                        skore = 3 + (sn or 0)
                    elif sn is not None and sn >= 0.8 and (ulice_ok or not k["_s"] or not su) and len(tokeny(r["nazevPuvodni"])) >= 1:
                        skore = 2 + sn if ulice_ok else (1.8 if len(tokeny(r["nazevPuvodni"]) & tokeny(k["_n"])) >= 2 else 0)
                if skore > 0:
                    dvojice.append((skore, ri, k["id"], d, sn))
        # nejlepší dvojice první; každý řádek i každá Kita nejvýš jednou
        dvojice.sort(key=lambda x: -x[0])
        pouz_r, pouz_k, spar = set(), set(), 0
        # slabé shody (jen blízkost) bereme, jen když v okolí není jiný kandidát
        pocet_k = Counter(x[1] for x in dvojice)
        pocet_kid = Counter(x[2] for x in dvojice)
        for skore, ri, kid, d, sn in dvojice:
            if ri in pouz_r or kid in pouz_k:
                continue
            if skore < 2 and (pocet_k[ri] > 1 or pocet_kid[kid] > 1) and skore < 1.45:
                continue
            # stejná adresa, ale oba názvy jsou výrazné a jiné: dvě Kity v jednom domě se nesmí splést
            if skore >= 3 and sn == 0 and (pocet_k[ri] > 1 or pocet_kid[kid] > 1):
                continue
            pouz_r.add(ri)
            pouz_k.add(kid)
            spar += 1
            r = radky[ri]
            if LADENI is not None:
                LADENI.append((round(skore, 2), z["id"], r["nazevPuvodni"], f"{r['ulice']} {r['cd']}", KID[kid]["_n"],
                               f"{KID[kid]['t'].get('addr:street', '')} {KID[kid]['_h']}", None if d is None else round(d)))
            v = vysledek.setdefault(kid, {"z": []})
            v["z"].append(zi)
            for pole in ("nazev", "ulice", "cd", "plz", "obec", "tel", "mail", "web", "traeger", "typ", "kap", "vek",
                         "provoz", "rollstuhl", "integrativ", "koncept"):
                if r.get(pole) not in (None, "") and v.get(pole) in (None, ""):
                    v[pole] = r[pole]
        nesparovano[z["id"]] = len(radky) - spar
        statistika.append((z["id"], len(radky), spar))
        print(f"{z['id']:22} řádků {len(radky):6}  spárováno {spar:6}  ({spar / max(1, len(radky)):.0%})", flush=True)

    json.dump({"zdroje": zdroje_out, "kity": vysledek, "nesparovano": nesparovano},
              open(os.path.join(OUT, "amt.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    # weby pro weby.py: z úředních dat a podle domény e-mailu
    FREEMAIL = re.compile(r"(^|\.)(gmx|web|t-online|gmail|googlemail|yahoo|hotmail|outlook|live|aol|freenet|arcor|"
                          r"online|posteo|mail|icloud|me|email|kabelbw|vodafone|versanet|unitybox|ewe|ewetel|htp-tel|"
                          r"osnanet|mailbox|msn|1und1|kabelmail|o2online|telekom|magenta|netcologne|t-systems)\.", re.I)
    extra, domeny = {}, Counter()
    kandid = {}
    for k in kity:
        t = k["t"]
        if t.get("website") or t.get("contact:website") or t.get("url"):
            continue
        a = vysledek.get(k["id"], {})
        if a.get("web"):
            extra[k["id"]] = {"url": a["web"], "zdroj": "amt"}
            continue
        m = (t.get("email") or t.get("contact:email") or a.get("mail") or "").split(";")[0].strip().lower()
        if "@" in m:
            dom = m.rsplit("@", 1)[1]
            if not FREEMAIL.search(dom + "."):
                kandid[k["id"]] = dom
                domeny[dom] += 1
    for kid, dom in kandid.items():
        # doména sdílená více Kitami patří zřizovateli (město, diakonie…) – tu nehádáme
        if domeny[dom] == 1:
            extra[kid] = {"url": f"https://www.{dom}" if dom.count(".") == 1 else f"https://{dom}", "zdroj": "mail"}
    json.dump(extra, open(os.path.join(OUT, "weby-extra.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    if LADENI is not None:
        json.dump(LADENI, open("/tmp/amt-ladeni.json", "w"), ensure_ascii=False)
    print("Kit s úředními údaji:", len(vysledek), "| weby navíc:", Counter(e["zdroj"] for e in extra.values()))
    pole = Counter()
    for v in vysledek.values():
        for p in ("tel", "mail", "web", "traeger", "kap", "ulice", "provoz", "vek"):
            if v.get(p) not in (None, ""):
                pole[p] += 1
    print("pole:", dict(pole))


if __name__ == "__main__":
    main()

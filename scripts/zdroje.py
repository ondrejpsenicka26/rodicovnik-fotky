"""Průzkum volných zdrojů pro školky, které zatím nemají fotku (zdroje/bez-fotek.json).

Použití: python3 scripts/zdroje.py osm | commons | letecke

osm      Weby školek a dětských skupin z OpenStreetMap pro celé Česko → zdroje/osm.json
         (© přispěvatelé OpenStreetMap, ODbL). Párování na školky se dělá až v datech webu.
commons  Fotky s volnou licencí z Wikimedia Commons v okolí školky, které mají v názvu,
         popisu nebo kategorii školku / školu → zdroje/commons.json. Ukládáme jen popis
         (autor, licence, odkaz), fotky samotné ne.
letecke  Ukázka leteckých snímků budov (Ortofoto ČR, ČÚZK, CC BY 4.0) pro školky
         ze zdroje/ukazka.json → ukazka/letecke/<slug>.jpg
"""

import json
import math
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "RodicovnikBot/1.0 (+https://github.com/ondrejpsenicka26/rodicovnik-fotky; prehled skolek v CR)"
lock = threading.Lock()


def log(*a):
    with lock:
        print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def stahni(url, data=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept-Language": "cs"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.headers.get("Content-Type", ""), r.read()


def bez_diakritiky(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def vzdalenost(a, b, c, d):
    r = 6371000
    p1, p2 = math.radians(a), math.radians(c)
    dp, dl = p2 - p1, math.radians(d - b)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


# ---------- OpenStreetMap ----------
DOTAZ = """
[out:json][timeout:300];
area["ISO3166-1"="CZ"][admin_level=2]->.cz;
(
  nwr(area.cz)["amenity"~"^(kindergarten|childcare)$"];
);
out center tags;
"""


def osm():
    prvky = None
    for pokus in range(4):
        for server in ("https://overpass-api.de", "https://overpass.private.coffee", "https://overpass.kumi.systems"):
            try:
                _, odp = stahni(f"{server}/api/interpreter", urllib.parse.urlencode({"data": DOTAZ}).encode(), 400)
                odp = json.loads(odp)
                if odp.get("remark"):
                    log("Overpass poznámka:", odp["remark"])
                if odp.get("elements"):
                    prvky = odp["elements"]
                    log("Overpass", server, "prvků:", len(prvky))
                    break
            except Exception as e:  # noqa: BLE001
                log("Overpass", server, e)
        if prvky:
            break
        time.sleep(40)
    if not prvky:
        raise SystemExit("Overpass nedostupný")
    out = []
    for e in prvky:
        t = e.get("tags", {})
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None:
            continue
        z = {"id": f"{e['type']}/{e['id']}", "nazev": t.get("name", ""), "lat": round(lat, 6), "lng": round(lon, 6)}
        for klic, tagy in (
            ("web", ("website", "contact:website", "url")),
            ("foto", ("image", "wikimedia_commons")),
            ("wikidata", ("wikidata",)),
        ):
            for tag in tagy:
                if t.get(tag):
                    z[klic] = t[tag].split(";")[0].strip()
                    break
        out.append(z)
    json.dump(out, open("zdroje/osm.json", "w"), ensure_ascii=False, separators=(",", ":"))
    log("OSM zařízení:", len(out), "s webem:", sum(1 for z in out if z.get("web")), "s fotkou:", sum(1 for z in out if z.get("foto")))


# ---------- Wikimedia Commons ----------
SILNE = re.compile(r"matersk|skolk|\bms\b|kindergarten|jesle|detsk[ae] skupin|lesni klub")
SLABE = re.compile(r"\bskol[aeyu]\b|\bzs\b|school|schule")
SPATNE = re.compile(
    r"pamatnik|pomnik|hrbitov|kostel|kaple|socha|kriz|znak|mapa|map of|logo|plaket|deska|interier kostela|"
    r"zastavka|nadrazi|vlak|tramvaj|autobus|hasic|pivovar|zamek|hrad\b"
)
LICENCE = re.compile(r"^(cc0|cc[ -]by|public domain|pd\b|attribution|fal|gfdl)", re.I)


def bez_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def commons_jedna(s):
    q = {
        "action": "query", "format": "json", "formatversion": "2", "generator": "geosearch",
        "ggscoord": f"{s['lat']}|{s['lng']}", "ggsradius": "150", "ggsnamespace": "6", "ggslimit": "40",
        "prop": "imageinfo|categories|coordinates", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "960",
        "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl|ImageDescription|DateTimeOriginal|AttributionRequired",
        "cllimit": "500", "clshow": "!hidden", "maxlag": "5",
    }  # fmt: skip
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(q)
    odp = None
    for pokus in range(4):
        try:
            odp = json.loads(stahni(url, timeout=40)[1])
            if "error" not in odp:
                break
            time.sleep(5 + 5 * pokus)
        except Exception as e:  # noqa: BLE001
            odp = {"chyba": str(e)}
            time.sleep(3 + 4 * pokus)
    if not odp or "query" not in odp:
        return s["slug"], [], (odp or {}).get("chyba") or (odp or {}).get("error")
    kand = []
    for p in odp["query"].get("pages", []):
        ii = (p.get("imageinfo") or [{}])[0]
        if ii.get("mime") not in ("image/jpeg", "image/png", "image/webp", "image/tiff"):
            continue
        if min(ii.get("width", 0), ii.get("height", 0)) < 600:
            continue
        em = ii.get("extmetadata") or {}
        lic = bez_html((em.get("LicenseShortName") or {}).get("value"))
        if not LICENCE.search(lic):
            continue
        kat = [c["title"].replace("Category:", "") for c in p.get("categories", [])]
        popis = bez_html((em.get("ImageDescription") or {}).get("value"))[:300]
        text_n = bez_diakritiky(p["title"])
        text_k = bez_diakritiky(" | ".join(kat))
        text_p = bez_diakritiky(popis)
        if SPATNE.search(text_n):
            continue
        sila = 0
        if SILNE.search(text_n) or SILNE.search(text_p):
            sila = 3
        elif SILNE.search(text_k):
            sila = 2
        elif SLABE.search(text_n) or SLABE.search(text_p) or SLABE.search(text_k):
            sila = 1
        if not sila:
            continue
        co = (p.get("coordinates") or [{}])[0]
        d = round(vzdalenost(s["lat"], s["lng"], co["lat"], co["lon"])) if "lat" in co else None
        kand.append({
            "titul": p["title"], "sila": sila, "m": d, "w": ii.get("width"), "h": ii.get("height"),
            "url": ii.get("thumburl") or ii.get("url"), "stranka": ii.get("descriptionurl"),
            "autor": bez_html((em.get("Artist") or {}).get("value"))[:120], "licence": lic,
            "licence_url": (em.get("LicenseUrl") or {}).get("value"),
            "datum": bez_html((em.get("DateTimeOriginal") or {}).get("value"))[:30], "popis": popis, "kat": kat[:12],
        })  # fmt: skip
    kand.sort(key=lambda k: (-k["sila"], k["m"] if k["m"] is not None else 999))
    return s["slug"], kand[:8], None


def commons():
    skolky = json.load(open("zdroje/bez-fotek.json"))
    limit = int(os.environ.get("LIMIT", "0"))
    if limit:
        skolky = skolky[:limit]
    vysledek, chyby, hotovo = {}, 0, 0
    with ThreadPoolExecutor(3) as ex:
        for slug, kand, chyba in ex.map(commons_jedna, skolky):
            hotovo += 1
            if chyba:
                chyby += 1
            if kand:
                vysledek[slug] = kand
            if hotovo % 500 == 0:
                log(f"{hotovo}/{len(skolky)}; s kandidátem {len(vysledek)}, chyb {chyby}")
    json.dump(vysledek, open("zdroje/commons.json", "w"), ensure_ascii=False, indent=0, sort_keys=True)
    sil = [max(k["sila"] for k in v) for v in vysledek.values()]
    log("Školek:", len(skolky), "s kandidátem:", len(vysledek), "silná shoda:", sum(1 for x in sil if x >= 2), "chyb:", chyby)


# ---------- letecké snímky (Ortofoto ČR, ČÚZK) ----------
def merkator(lat, lng):
    x = lng * 20037508.342789244 / 180
    y = math.log(math.tan((90 + lat) * math.pi / 360)) * 20037508.342789244 / math.pi
    return x, y


def letecke():
    from PIL import Image
    import io

    skolky = {s["slug"]: s for s in json.load(open("zdroje/bez-fotek.json"))}
    vyber = json.load(open("zdroje/ukazka.json"))
    os.makedirs("ukazka/letecke", exist_ok=True)
    try:
        info = json.loads(stahni("https://ags.cuzk.gov.cz/arcgis1/rest/services/ORTOFOTO/MapServer?f=pjson")[1])
        log("ORTOFOTO copyrightText:", info.get("copyrightText"), "| capabilities:", info.get("capabilities"))
    except Exception as e:  # noqa: BLE001
        log("ORTOFOTO info:", e)
    sirka_m, w, h = float(os.environ.get("SIRKA_M", "150")), 1200, 800
    for slug in vyber:
        s = skolky.get(slug)
        if not s:
            continue
        x, y = merkator(s["lat"], s["lng"])
        k = 1 / math.cos(math.radians(s["lat"]))  # metry v terénu → jednotky Mercatoru
        dx, dy = sirka_m * k / 2, sirka_m * k * h / w / 2
        bbox = f"{x - dx},{y - dy},{x + dx},{y + dy}"
        zdroje = [
            ("rest", "https://ags.cuzk.gov.cz/arcgis1/rest/services/ORTOFOTO/MapServer/export?"
             + urllib.parse.urlencode({"bbox": bbox, "bboxSR": "3857", "imageSR": "3857", "size": f"{w},{h}",
                                       "format": "jpg", "f": "image"})),
            ("wms", "https://ags.cuzk.gov.cz/arcgis1/services/ORTOFOTO/MapServer/WMSServer?"
             + urllib.parse.urlencode({"service": "WMS", "version": "1.3.0", "request": "GetMap", "layers": "0",
                                       "styles": "", "crs": "EPSG:3857", "bbox": bbox, "width": w, "height": h,
                                       "format": "image/jpeg"})),
        ]  # fmt: skip
        for jmeno, url in zdroje:
            try:
                ctype, data = stahni(url, timeout=90)
                im = Image.open(io.BytesIO(data)).convert("RGB")
                if im.getextrema() == ((255, 255), (255, 255), (255, 255)):
                    raise ValueError("prázdný snímek")
                im.save(f"ukazka/letecke/{slug}.jpg", quality=86)
                log(slug, jmeno, im.size, len(data))
                break
            except Exception as e:  # noqa: BLE001
                log(slug, jmeno, "chyba:", e)
        time.sleep(1)


if __name__ == "__main__":
    {"osm": osm, "commons": commons, "letecke": letecke}[sys.argv[1]]()

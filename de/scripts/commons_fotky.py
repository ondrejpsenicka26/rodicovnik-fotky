"""Fotky Kit z Wikimedia Commons (volné licence) – pro Kita Finder.

Zdroje:
  1) OSM tagy wikimedia_commons / image (File:…) přímo u Kity,
  2) soubory v kategorii „Kindergartens in Germany“ (a podkategoriích) se souřadnicemi,
     které leží do 60 m od Kity.
Bereme jen licence CC0, Public domain, CC BY, CC BY-SA. Fotky ukládáme zmenšené
(de/fotky/<osm id>/<n>.webp, 1280 px a náhled 640 px) a metadata s autorem a licencí
do de/data/fotky.json – web je u fotky uvádí.
"""
import io, json, math, os, re, time
from collections import defaultdict
import requests
from PIL import Image

OUT = os.path.join(os.path.dirname(__file__), "..")
API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "kita-finder-data/1.0 (https://github.com/ondrejpsenicka26/rodicovnik-fotky; contact via repo)"}
KOREN = ["Category:Kindergartens in Germany", "Category:Daycare centers in Germany"]
POVOLENE = re.compile(r"^(cc0|public domain|pd|cc by(-sa)? ?\d|cc-by(-sa)?-\d)", re.I)
VYLOUCIT = re.compile(r"kinder (spielen|beim|im)|children|kids|fest|feier|einweihung|richtfest|grundstein|spatenstich|"
                      r"interior|innen|logo|plan|karte|map|schild|sign", re.I)
MAX_NA_KITU = 3
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


def info(tituly):
    out = {}
    tituly = list(tituly)
    for i in range(0, len(tituly), 50):
        j = api(action="query", titles="|".join(tituly[i:i + 50]), prop="coordinates|imageinfo",
                iiprop="url|size|extmetadata|mime", iiurlwidth="1280", colimit="max")
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
    im.thumbnail((1280, 1280))
    os.makedirs(os.path.dirname(cil), exist_ok=True)
    im.save(cil, "WEBP", quality=78, method=5)
    nahled = im.copy()
    nahled.thumbnail((640, 640))
    nahled.save(cil.replace(".webp", "-nahled.webp"), "WEBP", quality=74, method=5)
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

    prirazeni = defaultdict(list)
    for titul, m in meta.items():
        if not m.get("thumb") or not POVOLENE.search(m["lic"] or "") or VYLOUCIT.search(titul):
            continue
        if m["mime"] not in ("image/jpeg", "image/png", "image/webp", "image/tiff"):
            continue
        cile = prime.get(titul)
        if not cile and m["lat"] is not None:
            k, d = nejblizsi(m["lat"], m["lon"])
            if k and d <= 60:
                cile = [k["id"]]
        for c in cile or []:
            prirazeni[c].append((titul, m))
    print("Kit s fotkou:", len(prirazeni), flush=True)

    vystup = {}
    for kid, seznam in prirazeni.items():
        polozky = []
        for n, (titul, m) in enumerate(seznam[:MAX_NA_KITU], 1):
            cil = os.path.join(OUT, "fotky", kid, f"{n}.webp")
            try:
                (w, h), barva = ulozit(m["thumb"], cil)
            except Exception as e:
                print("chyba", titul, e, flush=True)
                continue
            polozky.append({"n": n, "w": w, "h": h, "barva": barva, "titul": titul, "autor": m["autor"] or "Wikimedia Commons",
                            "licence": m["lic"], "licenceUrl": m["licUrl"], "url": m["page"]})
            time.sleep(0.3)
        if polozky:
            vystup[kid] = polozky
    json.dump(vystup, open(os.path.join(OUT, "data", "fotky.json"), "w"), ensure_ascii=False, indent=0)
    print("hotovo:", len(vystup), "Kit,", sum(len(v) for v in vystup.values()), "fotek", flush=True)


if __name__ == "__main__":
    main()

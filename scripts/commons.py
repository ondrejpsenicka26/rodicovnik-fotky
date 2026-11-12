"""Fotky budov školek z Wikimedia Commons (volné licence) pro školky bez vlastní fotky.

Vstup: zdroje/commons-vyber.json {slug: [{url, stranka, licence, licence_url, titul}]}
       – výběr z průzkumu scripts/zdroje.py commons (školka v názvu snímku, do 100 m od školky).
Výstup: fotky/<slug>/<n>.webp (+ náhled) a manifest/commons-<část>.json. U každé fotky je
        v manifestu `zdroj` (odkaz na stránku snímku a licence) – web ho u fotky uvádí.
Lidi na fotkách pak hledá scripts/kontrola.py a všechny fotky ještě prohlédne člověk.
"""

import io
import json
import os
import sys
import time
import urllib.request

from PIL import Image

sys.path.insert(0, os.path.dirname(__file__))

UA = "RodicovnikBot/1.0 (+https://github.com/ondrejpsenicka26/rodicovnik-fotky; prehled skolek v CR)"
CAST = int(os.environ.get("CAST", "0"))
CASTI = int(os.environ.get("CASTI", "1"))


def stahni(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def dhash(im, n=8):
    g = im.convert("L").resize((n + 1, n), Image.LANCZOS)
    px = list(g.tobytes())
    bity = 0
    for r in range(n):
        for c in range(n):
            bity = (bity << 1) | (px[r * (n + 1) + c] > px[r * (n + 1) + c + 1])
    return bity


def barva_a_lqip(im, s_obrazkem):
    import base64

    r, g, b = im.resize((1, 1), Image.LANCZOS).getpixel((0, 0))
    out = {"barva": f"#{r:02x}{g:02x}{b:02x}"}
    if s_obrazkem:
        w = 20
        h = max(1, round(w * im.height / im.width))
        buf = io.BytesIO()
        im.resize((w, h), Image.LANCZOS).save(buf, "JPEG", quality=45, optimize=True)
        out["lqip"] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return out


def main():
    vyber = json.load(open("zdroje/commons-vyber.json"))
    uz = set()
    for f in os.listdir("manifest"):
        if f.endswith(".json"):
            uz |= set(json.load(open(f"manifest/{f}")))
    slugy = [s for s in sorted(vyber) if s not in uz][CAST::CASTI]
    print("Školek:", len(slugy), flush=True)
    manifest, chyb = {}, 0
    for slug in slugy:
        zaznamy = []
        for k in vyber[slug]:
            data = None
            # větší náhled, když ho Commons má; jinak ten z průzkumu (960 px)
            for url in (k["url"].replace("/960px-", "/1280px-"), k["url"]):
                for pokus in range(3):
                    try:
                        data = stahni(url)
                        break
                    except Exception as e:  # noqa: BLE001
                        if "404" in str(e) or "400" in str(e):
                            break
                        time.sleep(4 + 6 * pokus)
                if data:
                    break
                time.sleep(0.5)
            time.sleep(0.6)
            if not data:
                chyb += 1
                print("nestaženo:", slug, k["titul"], flush=True)
                continue
            try:
                src = Image.open(io.BytesIO(data)).convert("RGB")
            except Exception as e:  # noqa: BLE001
                chyb += 1
                print("nečitelné:", slug, e, flush=True)
                continue
            n = len(zaznamy) + 1
            os.makedirs(f"fotky/{slug}", exist_ok=True)
            im = src.copy()
            im.thumbnail((1200, 1200))
            im.save(f"fotky/{slug}/{n}.webp", "WEBP", quality=72, method=6)
            z = {
                "soubor": f"{n}.webp", "w": im.size[0], "h": im.size[1], "typ": "budova", "jistota": 1,
                "url": k["url"].split("?")[0], "stranka": k["stranka"], "alt": "commons", "ms": True,
                "dh": f"{dhash(src):016x}",
                "zdroj": {"nazev": "Wikimedia Commons", "url": k["stranka"], "licence": k["licence"],
                          "licence_url": k.get("licence_url") or ""},
            }  # fmt: skip
            if n == 1:
                t = src.copy()
                t.thumbnail((560, 560))
                t.save(f"fotky/{slug}/1-nahled.webp", "WEBP", quality=68, method=6)
                z["nahled"] = "1-nahled.webp"
            z.update(barva_a_lqip(im, n == 1))
            zaznamy.append(z)
        if zaznamy:
            manifest[slug] = zaznamy
    json.dump(manifest, open(f"manifest/commons-{CAST}.json", "w"), ensure_ascii=False, indent=0, sort_keys=True)
    print("Hotovo: školek", len(manifest), "fotek", sum(len(v) for v in manifest.values()), "chyb", chyb)


if __name__ == "__main__":
    main()

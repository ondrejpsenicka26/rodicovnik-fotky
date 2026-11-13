"""Ilustrační fotky pro web (vygenerované v Canvě, nejsou to skutečné školky ani děti).

Čte ilustrace/zdroje.json: [{"slug": "...", "url": "https://..."}] – url je dočasný odkaz
na export z Canvy (JPG nebo ZIP s jedním JPG). Uloží ilustrace/<slug>.webp (šířka 1600 px)
a ilustrace/<slug>-640.webp a zapíše ilustrace/manifest.json (rozměry a průměrná barva).
Odkazy po stažení ze zdroje.json maže, aby v repu nezůstaly.
"""

import io
import json
import os
import sys
import urllib.request
import zipfile

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "ilustrace")


def stahni(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "rodicovnik-ilustrace/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def obrazek(data: bytes) -> Image.Image:
    if data[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(data))
        jmena = sorted(n for n in z.namelist() if n.lower().endswith((".jpg", ".jpeg", ".png")))
        if not jmena:
            raise ValueError("ZIP bez obrázku")
        data = z.read(jmena[0])
    im = Image.open(io.BytesIO(data))
    im.load()
    return im.convert("RGB")


def uloz(im: Image.Image, cesta: str, sirka: int, kvalita: int) -> tuple[int, int]:
    if im.width > sirka:
        im = im.resize((sirka, round(im.height * sirka / im.width)), Image.LANCZOS)
    im.save(cesta, "WEBP", quality=kvalita, method=6)
    return im.size


def main() -> int:
    zdroje_cesta = os.path.join(DIR, "zdroje.json")
    zdroje = json.load(open(zdroje_cesta))
    manifest_cesta = os.path.join(DIR, "manifest.json")
    manifest = json.load(open(manifest_cesta)) if os.path.exists(manifest_cesta) else {}
    chyby = 0
    for z in zdroje:
        slug = z["slug"]
        try:
            im = obrazek(stahni(z["url"]))
        except Exception as e:  # noqa: BLE001
            print(f"{slug}: {e}", file=sys.stderr)
            chyby += 1
            continue
        w, h = uloz(im, os.path.join(DIR, f"{slug}.webp"), 1600, 82)
        uloz(im, os.path.join(DIR, f"{slug}-640.webp"), 640, 78)
        r, g, b = im.resize((1, 1), Image.BOX).getpixel((0, 0))
        manifest[slug] = {"w": w, "h": h, "barva": f"#{r:02x}{g:02x}{b:02x}", "puvodni": list(im.size)}
        print(f"{slug}: {im.size} -> {w}×{h}")
    json.dump(manifest, open(manifest_cesta, "w"), ensure_ascii=False, indent=1, sort_keys=True)
    # dočasné odkazy z repa pryč
    json.dump([], open(zdroje_cesta, "w"))
    return 1 if chyby == len(zdroje) and zdroje else 0


if __name__ == "__main__":
    sys.exit(main())

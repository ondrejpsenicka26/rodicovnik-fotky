"""Doplní náhled (560 px) a LQIP první fotce školky, pokud ho nemá
(po vyřazení původní první fotky)."""

import base64
import glob
import io
import json

from PIL import Image

doplneno = 0
for mf in glob.glob("manifest/*.json"):
    m = json.load(open(mf))
    for slug, fotky in m.items():
        if not fotky or fotky[0].get("nahled"):
            continue
        f = fotky[0]
        im = Image.open(f"fotky/{slug}/{f['soubor']}").convert("RGB")
        t = im.copy()
        t.thumbnail((560, 560))
        nazev = f["soubor"].replace(".webp", "-nahled.webp")
        t.save(f"fotky/{slug}/{nazev}", "WEBP", quality=68, method=6)
        f["nahled"] = nazev
        w = 20
        h = max(1, round(w * im.height / im.width))
        buf = io.BytesIO()
        im.resize((w, h), Image.LANCZOS).save(buf, "JPEG", quality=45, optimize=True)
        f["lqip"] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        doplneno += 1
    json.dump(m, open(mf, "w"), ensure_ascii=False, indent=0, sort_keys=True)
print("Doplněno náhledů:", doplneno)

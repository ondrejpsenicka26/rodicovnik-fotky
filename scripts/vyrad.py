"""Vyřadí fotky po ruční kontrole: smaže soubory, odebere je z manifestů a zapíše
do vyrazene.json (s důvodem), aby se dalo dohledat, co a proč zmizelo.

Použití: python3 scripts/vyrad.py seznam.json "důvod"
  seznam.json  ["fotky/<slug>/<n>.webp", …]
"""

import glob
import json
import os
import sys

seznam = json.load(open(sys.argv[1]))
duvod = sys.argv[2] if len(sys.argv) > 2 else "ruční kontrola"
zaznam = json.load(open("vyrazene.json")) if os.path.exists("vyrazene.json") else {}
podle_slugu = {}
for cesta in seznam:
    _, slug, soubor = cesta.split("/")
    podle_slugu.setdefault(slug, set()).add(soubor)
    zaznam[cesta] = duvod

for mf in glob.glob("manifest/*.json"):
    m = json.load(open(mf))
    zmena = False
    for slug, soubory in podle_slugu.items():
        if slug not in m:
            continue
        m[slug] = [f for f in m[slug] if f["soubor"] not in soubory]
        zmena = True
        if not m[slug]:
            del m[slug]
    if zmena:
        json.dump(m, open(mf, "w"), ensure_ascii=False, indent=0, sort_keys=True)

smazano = 0
for slug, soubory in podle_slugu.items():
    for s in soubory:
        for p in [f"fotky/{slug}/{s}"] + ([f"fotky/{slug}/1-nahled.webp"] if s == "1.webp" else []):
            if os.path.exists(p):
                os.remove(p)
                smazano += 1
    if os.path.isdir(f"fotky/{slug}") and not os.listdir(f"fotky/{slug}"):
        os.rmdir(f"fotky/{slug}")
json.dump(zaznam, open("vyrazene.json", "w"), ensure_ascii=False, indent=0, sort_keys=True)
print("Vyřazeno fotek:", len(seznam), "smazáno souborů:", smazano)

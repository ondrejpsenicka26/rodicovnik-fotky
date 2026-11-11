"""Druhá, přísnější kontrola, že na fotkách nejsou lidé.

Hlavní stahování používá YOLO11s (práh 0,15). Tady jede větší model YOLO11x ve vyšším
rozlišení s nižším prahem a navíc na zvětšených výřezech (malé postavy v pozadí).
Výsledek: kontrola.json {"fotky/<slug>/<n>.webp": nejvyšší jistota osoby}. Fotky
s jistotou ≥ 0,10 pak člověk prohlédne a sporné vyřadí.
"""

import glob
import json
import os

from PIL import Image
from ultralytics import YOLO

model = YOLO("yolo11x.pt")
vysledek = {}
soubory = [f for f in sorted(glob.glob("fotky/*/*.webp")) if "nahled" not in f]
print("Fotek:", len(soubory), flush=True)
for i, f in enumerate(soubory):
    im = Image.open(f).convert("RGB")
    w, h = im.size
    # celá fotka + 4 překrývající se čtvrtiny (zvětšené), ať se chytí i malé postavy
    vyrezy = [im] + [
        im.crop((int(x * w * 0.45), int(y * h * 0.45), int(x * w * 0.45 + w * 0.55), int(y * h * 0.45 + h * 0.55)))
        for x in (0, 1)
        for y in (0, 1)
    ]
    nejvic = 0.0
    for v in vyrezy:
        r = model.predict(v, imgsz=1280, conf=0.08, classes=[0], verbose=False)[0]
        if len(r.boxes):
            nejvic = max(nejvic, float(r.boxes.conf.max()))
    if nejvic >= 0.08:
        vysledek[f] = round(nejvic, 3)
    if i % 100 == 0:
        print(f"{i}/{len(soubory)}, podezřelých {len(vysledek)}", flush=True)
json.dump(vysledek, open("kontrola.json", "w"), indent=0, sort_keys=True)
print("Hotovo, podezřelých:", len(vysledek))

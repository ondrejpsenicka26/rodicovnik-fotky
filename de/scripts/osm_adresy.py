"""Adresy v okolí Kit, které v OpenStreetMap nemají vyplněnou ulici nebo PSČ.

Pro každou takovou Kitu najde adresní body a budovy do 40 m od jejího obrysu (Overpass)
a uloží nejbližší z nich. Z toho se v build.py odvodí ulice a PSČ (číslo domu jen tehdy,
když je jednoznačné). Zdroj: © přispěvatelé OpenStreetMap (ODbL).
Výstup: de/data/adresy-okoli.json  {osm id: [[ulice, číslo, PSČ, obec, vzdálenost m], …]}
"""
import json, math, os, threading, time
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

import requests

OUT = os.path.join(os.path.dirname(__file__), "..", "data")
UA = {"User-Agent": "kita-finder-data/1.0 (github.com/ondrejpsenicka26/rodicovnik-fotky)"}
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]
DAVKA = 100
CIL = os.path.join(OUT, "adresy-okoli.json")


def vzd(a, b, c, d):
    x = math.radians(d - b) * math.cos(math.radians((a + c) / 2))
    y = math.radians(c - a)
    return 6371000 * math.hypot(x, y)


def dotaz(ids):
    n = ",".join(i[1:] for i in ids if i[0] == "n")
    w = ",".join(i[1:] for i in ids if i[0] == "w")
    r = ",".join(i[1:] for i in ids if i[0] == "r")
    casti = "".join(f"{t}(id:{s});" for t, s in (("node", n), ("way", w), ("relation", r)) if s)
    return f"""[out:json][timeout:120];
({casti})->.k;
nwr(around.k:40)["addr:housenumber"]["addr:street"];
out tags center qt;"""


def overpass(q, prvni=0):
    for pokus in range(8):
        url = OVERPASS[(prvni + pokus) % len(OVERPASS)]
        try:
            r = requests.post(url, data={"data": q}, headers=UA, timeout=150)
            if r.status_code == 200 and r.text.lstrip().startswith("{"):
                return r.json()["elements"]
            print("  overpass", url, r.status_code, flush=True)
        except Exception as e:  # noqa: BLE001
            print("  overpass", url, str(e)[:100], flush=True)
        time.sleep(5 * (pokus + 1))
    return None


def main():
    kity = json.load(open(os.path.join(OUT, "kitas-osm.json")))
    chybi = [k for k in kity if not k["t"].get("addr:street") or not k["t"].get("addr:postcode")]
    try:
        vystup = json.load(open(CIL))
    except Exception:  # noqa: BLE001
        vystup = {}
    zbyva = [k for k in chybi if k["id"] not in vystup]
    # nejdřív Kity se jménem a bez ulice (ty se na webu ukazují a adresa jim chybí celá),
    # blízké Kity do stejné dávky: Overpass pak hledá v malém území
    zbyva.sort(key=lambda k: (bool(k["t"].get("addr:street")) or not k["t"].get("name"),
                              round(k["lat"], 1), round(k["lon"], 1), k["lat"]))
    print("bez adresy:", len(chybi), "zbývá:", len(zbyva), flush=True)
    zamek, stav, konec = threading.Lock(), {"hotovo": 0, "chyby": 0}, time.time() + 215 * 60

    def zpracuj(arg):
        cislo, davka = arg
        if time.time() > konec or stav["chyby"] >= 6:
            return
        el = overpass(dotaz([k["id"] for k in davka]), cislo)
        if el is None:
            with zamek:
                stav["chyby"] += 1
            return
        adresy = []
        for e in el:
            t = e.get("tags") or {}
            la = e.get("lat") or (e.get("center") or {}).get("lat")
            lo = e.get("lon") or (e.get("center") or {}).get("lon")
            if la is None or not t.get("addr:street"):
                continue
            adresy.append((la, lo, t["addr:street"].strip(), t.get("addr:housenumber", "").strip(),
                           t.get("addr:postcode", "").strip(), (t.get("addr:city") or "").strip()))
        mriz = defaultdict(list)
        for a in adresy:
            mriz[(round(a[0] * 200), round(a[1] * 200))].append(a)
        nove = {}
        for k in davka:
            kand = []
            gx, gy = round(k["lat"] * 200), round(k["lon"] * 200)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for a in mriz.get((gx + dx, gy + dy), ()):
                        d = vzd(k["lat"], k["lon"], a[0], a[1])
                        if d <= 150:
                            kand.append([a[2], a[3], a[4], a[5], round(d)])
            kand.sort(key=lambda x: x[4])
            vid, out = set(), []
            for x in kand:
                klic = (x[0], x[1])
                if klic in vid:
                    continue
                vid.add(klic)
                out.append(x)
            nove[k["id"]] = out[:8]
        with zamek:
            vystup.update(nove)
            stav["hotovo"] += 1
            if stav["hotovo"] % 5 == 0:
                json.dump(vystup, open(CIL, "w"), ensure_ascii=False, separators=(",", ":"))
                print(f"{len(vystup)} / {len(chybi)}", flush=True)

    davky = [(n, zbyva[i:i + DAVKA]) for n, i in enumerate(range(0, len(zbyva), DAVKA))]
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(zpracuj, davky))
    json.dump(vystup, open(CIL, "w"), ensure_ascii=False, separators=(",", ":"))
    print("hotovo:", len(vystup), "s kandidátem:", sum(1 for v in vystup.values() if v), flush=True)


if __name__ == "__main__":
    main()

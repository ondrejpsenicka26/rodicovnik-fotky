"""Weby školek z portálu České školní inspekce (portal.csicr.cz/School/<RED_IZO>).

Vstup:  weby-csi/vstup.json   {slug: red_izo}
Výstup: weby-csi/vystup.json  {slug: "https://…" | null}
Ohleduplně: řídí se robots.txt, jeden požadavek za ~1,2 s.
"""

import html
import json
import os
import re
import time
import urllib.request
import urllib.robotparser

UA = "RodicovnikBot/1.0 (+https://rodicovnik.cz; prehled skolek)"
BASE = "https://portal.csicr.cz/School/"
vstup = json.load(open("weby-csi/vstup.json"))
vystup = json.load(open("weby-csi/vystup.json")) if os.path.exists("weby-csi/vystup.json") else {}

rp = urllib.robotparser.RobotFileParser()
try:
    req = urllib.request.Request("https://portal.csicr.cz/robots.txt", headers={"User-Agent": UA})
    rp.parse(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "replace").splitlines())
except Exception:  # noqa: BLE001
    rp = None

cache = {}
START, LIMIT = time.time(), 330 * 60
for i, (slug, red) in enumerate(sorted(vstup.items())):
    if slug in vystup:
        continue
    if time.time() - START > LIMIT:
        break
    if red not in cache:
        u = BASE + red
        if rp is not None and not rp.can_fetch(UA, u):
            print("robots.txt zakazuje", u)
            cache[red] = None
        else:
            try:
                r = urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "cs"}), timeout=30)
                doc = r.read(2_000_000).decode("utf-8", "replace")
                m = re.search(r"Web:\s*(?:</[^>]+>\s*)*<a[^>]+href=\"([^\"]+)\"", doc) or re.search(r"Web:\s*<a[^>]*>([^<]+)</a>", doc)
                web = html.unescape(m.group(1)).strip() if m else None
                if web and not web.startswith("http"):
                    web = "http://" + web
                cache[red] = web
            except Exception as e:  # noqa: BLE001
                print("chyba", red, e)
                cache[red] = "CHYBA"
            time.sleep(1.2)
    if cache[red] != "CHYBA":
        vystup[slug] = cache[red]
    if i % 100 == 0:
        json.dump(vystup, open("weby-csi/vystup.json", "w"), ensure_ascii=False, indent=0, sort_keys=True)
        print(i, len(vystup), sum(1 for v in vystup.values() if v))
json.dump(vystup, open("weby-csi/vystup.json", "w"), ensure_ascii=False, indent=0, sort_keys=True)
print("hotovo", len(vystup), "s webem", sum(1 for v in vystup.values() if v))

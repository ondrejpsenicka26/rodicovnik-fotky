"""Ověří nově dohledané weby školek: otevře jednou úvodní stránku a zapíše, co na ní je.

Vstup:  weby-csi/overit.json   {slug: "https://…"}
Výstup: weby-csi/overeni.json  {slug: {"ok": bool, "duvod": str, "final": url, "titulek": str}}

ok = stránka se načetla, nejde o zaparkovanou/prodejní doménu ani cizí obsah (kasino, sázky)
a text zmiňuje školku, mateřskou školu nebo dětskou skupinu.
Jedna žádost na web, řídí se robots.txt, identifikuje se.
"""

import html
import json
import os
import re
import urllib.parse
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor

UA = "RodicovnikBot/1.0 (+https://github.com/ondrejpsenicka26/rodicovnik-fotky; prehled skolek v CR)"
vstup = json.load(open("weby-csi/overit.json"))
vystup = json.load(open("weby-csi/overeni.json")) if os.path.exists("weby-csi/overeni.json") else {}

CIZI = re.compile(
    r"casino|kasino|sázk|sazk|\bbet\b|betting|poker|slot|viagra|cialis|porn|escort|loan|crypto|bitcoin|"
    r"doména je na prodej|domena je na prodej|domain (is )?for sale|tato doména|this domain|parkovan|parked|"
    r"buy this domain|koupit doménu|webhosting|hosting zdarma|stránka nenalezena|page not found|"
    r"forpsi|wedos|active24|websupport|godaddy|sedo\.com|dan\.com",
    re.I,
)
SKOLKA = re.compile(r"mateřsk|materske|materska|školk|skolk|školič|dětsk[áé] skupin|detska skupina|\bMŠ\b|předškol|kindergarten|jesle|jesl", re.I)


def robots_ok(u):
    p = urllib.parse.urlparse(u)
    rp = urllib.robotparser.RobotFileParser()
    try:
        req = urllib.request.Request(f"{p.scheme}://{p.netloc}/robots.txt", headers={"User-Agent": UA})
        rp.parse(urllib.request.urlopen(req, timeout=15).read(300_000).decode("utf-8", "replace").splitlines())
        return rp.can_fetch(UA, u)
    except Exception:  # noqa: BLE001
        return True


def over(item):
    slug, web = item
    try:
        if not robots_ok(web):
            return slug, {"ok": False, "duvod": "robots"}
        req = urllib.request.Request(web, headers={"User-Agent": UA, "Accept-Language": "cs"})
        with urllib.request.urlopen(req, timeout=25) as r:
            final = r.geturl()
            data = r.read(3_000_000)
            ct = r.headers.get("Content-Type", "")
        m = re.search(r"charset=([\w-]+)", ct, re.I) or re.search(rb'charset=["\']?([\w-]+)', data[:3000], re.I)
        enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
        try:
            t = data.decode(enc, errors="replace")
        except LookupError:
            t = data.decode("utf-8", errors="replace")
        tit = re.search(r"<title[^>]*>(.*?)</title>", t, re.I | re.S)
        titulek = html.unescape(re.sub(r"\s+", " ", tit.group(1))).strip()[:120] if tit else ""
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.I | re.S)
        text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        text = re.sub(r"\s+", " ", text)
        if CIZI.search(titulek) or (len(text) < 3000 and CIZI.search(text)):
            return slug, {"ok": False, "duvod": "cizi", "final": final, "titulek": titulek}
        if not SKOLKA.search(titulek + " " + text[:200_000]):
            return slug, {"ok": False, "duvod": "bez-skolky", "final": final, "titulek": titulek}
        return slug, {"ok": True, "duvod": "", "final": final, "titulek": titulek}
    except Exception as e:  # noqa: BLE001
        return slug, {"ok": False, "duvod": "chyba: " + str(e)[:80]}


zbyva = [(s, w) for s, w in sorted(vstup.items()) if s not in vystup]
print("webů k ověření", len(zbyva), flush=True)
with ThreadPoolExecutor(8) as ex:
    for n, (s, v) in enumerate(ex.map(over, zbyva)):
        vystup[s] = v
        if n % 200 == 0:
            print(n, flush=True)
json.dump(vystup, open("weby-csi/overeni.json", "w"), ensure_ascii=False, indent=0, sort_keys=True)
print("hotovo", len(vystup), "ok", sum(1 for v in vystup.values() if v["ok"]))

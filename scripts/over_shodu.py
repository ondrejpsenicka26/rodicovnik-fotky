"""Ověří, že nalezený web patří právě této školce: na úvodní nebo kontaktní stránce hledá IČO, PSČ nebo ulici.

Vstup:  weby-csi/shoda-vstup.json  {slug: {"web": url, "ico": "…", "psc": "…", "ulice": "…"}}
Výstup: weby-csi/shoda.json        {slug: {"ok": bool, "shoda": "ico|psc|ulice|", "duvod": str, "final": url, "titulek": str}}

Na web jdou nejvýš 4 žádosti (úvod + až 3 stránky typu kontakt / o škole), řídí se robots.txt.
"""

import html
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor

UA = "RodicovnikBot/1.0 (+https://github.com/ondrejpsenicka26/rodicovnik-fotky; prehled skolek v CR)"
OPAKOVAT = os.environ.get("OPAKOVAT", "") == "1"
OPAK_RE = re.compile(r"Errno 101|timed out|429|CERTIFICATE_VERIFY|TLSV1_ALERT|DH_KEY|HANDSHAKE|Remote end|Error 500")
if OPAKOVAT:
    # Runnery nemají IPv6; chyba IPv6 („Network is unreachable“) pak zakryje skutečný výsledek IPv4.
    import socket
    _gai = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [x for x in _gai(*a, **k) if x[0] == socket.AF_INET] or _gai(*a, **k)
vstup = json.load(open("weby-csi/shoda-vstup.json"))
vystup = json.load(open("weby-csi/shoda.json")) if os.path.exists("weby-csi/shoda.json") else {}
CIZI = re.compile(r"casino|kasino|sázk|poker|viagra|porn|escort|doména je na prodej|domain (is )?for sale|"
                  r"buy this domain|parkovan|parked", re.I)
KONTAKT = re.compile(r"kontakt|o-nas|o-skol|o-skolc|zakladni-udaje|zakladni-informace|informace|uvod|o-ms|skolka", re.I)


def bez(s):
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


_robots = {}


def smim(u):
    p = urllib.parse.urlparse(u)
    k = f"{p.scheme}://{p.netloc}"
    if k not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(k + "/robots.txt", headers={"User-Agent": UA})
            rp.parse(urllib.request.urlopen(req, timeout=15).read(300_000).decode("utf-8", "replace").splitlines())
        except Exception:  # noqa: BLE001
            rp = None
        _robots[k] = rp
    rp = _robots[k]
    return True if rp is None else rp.can_fetch(UA, u)


def nacti(u, pokus=0):
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "cs"})
    try:
        r = urllib.request.urlopen(req, timeout=45 if OPAKOVAT else 25)
    except urllib.error.HTTPError as e:
        if OPAKOVAT and e.code == 429 and pokus == 0:
            time.sleep(40)
            return nacti(u, 1)
        raise
    with r:
        final = r.geturl()
        data = r.read(3_000_000)
        ct = r.headers.get("Content-Type", "")
    m = re.search(r"charset=([\w-]+)", ct, re.I) or re.search(rb'charset=["\']?([\w-]+)', data[:3000], re.I)
    enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
    try:
        return final, data.decode(enc, errors="replace")
    except LookupError:
        return final, data.decode("utf-8", errors="replace")


def text_z(t):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.I | re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", t)))


def shoda(text, z):
    t = bez(text)
    cisla = re.sub(r"\s", "", t)
    if z.get("ico") and z["ico"].lstrip("0") and z["ico"] in cisla:
        return "ico"
    if z.get("psc") and re.sub(r"\D", "", z["psc"]) in cisla:
        return "psc"
    ul = bez(z.get("ulice") or "")
    if len(ul) >= 5 and ul in t:
        return "ulice"
    return ""


def over(item):
    slug, z = item
    web = z["web"]
    try:
        if not smim(web):
            return slug, {"ok": False, "shoda": "", "duvod": "robots"}
        pouzit = ""
        try:
            final, t = nacti(web)
        except urllib.error.URLError as e:
            # Neplatný certifikát: zkusí stejnou adresu přes http (odkaz pak vede tam, kde se web načte bez varování).
            if not (OPAKOVAT and web.startswith("https://") and "SSL" in str(e)):
                raise
            pouzit = "http://" + web[len("https://"):]
            final, t = nacti(pouzit)
            if final.startswith("https://"):
                raise RuntimeError("http vede zpět na https")
        tit = re.search(r"<title[^>]*>(.*?)</title>", t, re.I | re.S)
        titulek = html.unescape(re.sub(r"\s+", " ", tit.group(1))).strip()[:120] if tit else ""
        text = text_z(t)
        if CIZI.search(titulek) or (len(text) < 3000 and CIZI.search(text)):
            return slug, {"ok": False, "shoda": "", "duvod": "cizi", "final": final, "titulek": titulek}
        s = shoda(text, z)
        if not s:
            host = urllib.parse.urlparse(final).netloc
            odkazy = []
            for m in re.finditer(r'<a[^>]+href=["\']([^"\'#]+)["\']', t, re.I):
                u = urllib.parse.urljoin(final, html.unescape(m.group(1)))
                if urllib.parse.urlparse(u).netloc == host and KONTAKT.search(bez(urllib.parse.unquote(u))) and u not in odkazy:
                    odkazy.append(u)
            odkazy.sort(key=lambda u: 0 if "kontakt" in u.lower() else 1)
            for u in odkazy[:3]:
                if not smim(u):
                    continue
                time.sleep(1)
                try:
                    _, t2 = nacti(u)
                except Exception:  # noqa: BLE001
                    continue
                s = shoda(text_z(t2), z)
                if s:
                    break
        v = {"ok": bool(s), "shoda": s, "duvod": "" if s else "bez-shody", "final": final, "titulek": titulek}
        if pouzit:
            v["web_pouzit"] = pouzit
        return slug, v
    except Exception as e:  # noqa: BLE001
        return slug, {"ok": False, "shoda": "", "duvod": "chyba: " + str(e)[:80]}


zbyva = [(s, z) for s, z in sorted(vstup.items()) if s not in vystup or vystup[s].get("web") != z["web"]
         or (OPAKOVAT and not vystup[s].get("opak") and OPAK_RE.search(vystup[s].get("duvod") or ""))]
print("webů k ověření", len(zbyva), flush=True)
with ThreadPoolExecutor(4) as ex:
    for n, (s, v) in enumerate(ex.map(over, zbyva)):
        v["web"] = vstup[s]["web"]
        if OPAKOVAT:
            v["opak"] = 1
        vystup[s] = v
        if n % 100 == 0:
            print(n, flush=True)
json.dump(vystup, open("weby-csi/shoda.json", "w"), ensure_ascii=False, indent=0, sort_keys=True)
print("hotovo", len(vystup), "ok", sum(1 for v in vystup.values() if v["ok"]))

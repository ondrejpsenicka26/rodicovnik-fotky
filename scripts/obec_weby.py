"""Hledá stránku školky na webu obce (obec<obec>.cz, <obec>.cz …).

Z úvodní stránky obce vezme nejvýš 6 odkazů na školku (mateřská škola, školka, MŠ, u ZŠ i škola)
a stránku přijme, jen když je na ní IČO školky, nebo ulice spolu s PSČ a slovo „mateřsk“.
Respektuje robots.txt.
Vstup:  hledani/obec-vstup.json  {slug: {domeny: [...], ico, psc, ulice, obec, zs}}
Výstup: hledani/obec.json        {slug: {web, shoda, titulek} | null}
"""
import html
import json
import os
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor

UA = "RodicovnikBot/1.0 (+https://github.com/ondrejpsenicka26/rodicovnik-fotky; prehled skolek v CR)"
vstup = json.load(open("hledani/obec-vstup.json"))
VYSTUP = "hledani/obec.json"
vystup = json.load(open(VYSTUP)) if os.path.exists(VYSTUP) else {}
_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [x for x in _gai(*a, **k) if x[0] == socket.AF_INET] or _gai(*a, **k)


def bez(s):
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def existuje(host):
    try:
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False


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


def nacti(u):
    req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept-Language": "cs"})
    with urllib.request.urlopen(req, timeout=25) as r:
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
    ul = bez(z.get("ulice") or "")
    if len(ul) >= 5 and ul in t and z.get("psc") and re.sub(r"\D", "", z["psc"]) in cisla and "matersk" in t:
        return "ulice+psc"
    return ""


def hledej(item):
    slug, z = item
    obec = bez(z["obec"])
    vzor = re.compile(r"matersk|skolk|(^|[^a-z])ms([^a-z]|$)" + (r"|skola|(^|[^a-z])zs([^a-z]|$)" if z.get("zs") else ""))
    for dom in z["domeny"]:
        host = dom if existuje(dom) else ("www." + dom if existuje("www." + dom) else None)
        if not host:
            continue
        domu = None
        for sch in ("https://", "http://"):
            try:
                if not smim(sch + host + "/"):
                    break
                domu = nacti(sch + host + "/")
                break
            except Exception:  # noqa: BLE001
                continue
        if not domu:
            continue
        final, t = domu
        if obec not in bez(text_z(t)):
            continue  # web jiné obce nebo cizí doména
        fh = urllib.parse.urlparse(final).netloc
        odkazy = []
        for m in re.finditer(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', t, re.I | re.S):
            u = urllib.parse.urljoin(final, html.unescape(m.group(1)))
            pu = urllib.parse.urlparse(u)
            if pu.netloc != fh and not pu.netloc.endswith("." + fh.removeprefix("www.")):
                continue
            popis = bez(urllib.parse.unquote(pu.path) + " " + text_z(m.group(2)))
            if vzor.search(popis) and u not in odkazy and not re.search(r"\.(pdf|docx?|jpe?g|png)$", pu.path, re.I):
                odkazy.append(u)
        odkazy.sort(key=lambda u: 0 if "matersk" in bez(u) or "skolk" in bez(u) else 1)
        for u in odkazy[:6]:
            if not smim(u):
                continue
            time.sleep(1)
            try:
                f2, t2 = nacti(u)
            except Exception:  # noqa: BLE001
                continue
            s = shoda(text_z(t2), z)
            if s:
                tit = re.search(r"<title[^>]*>(.*?)</title>", t2, re.I | re.S)
                titulek = html.unescape(re.sub(r"\s+", " ", tit.group(1))).strip()[:120] if tit else ""
                return slug, {"web": f2.split("#")[0], "shoda": s, "titulek": titulek}
    return slug, None


zbyva = [(s, z) for s, z in sorted(vstup.items()) if s not in vystup]
print("školek", len(zbyva), flush=True)
with ThreadPoolExecutor(8) as ex:
    for n, (s, v) in enumerate(ex.map(hledej, zbyva)):
        vystup[s] = v
        if n % 50 == 0:
            print(n, sum(1 for x in vystup.values() if x), flush=True)
            json.dump(vystup, open(VYSTUP, "w"), ensure_ascii=False, indent=0, sort_keys=True)
json.dump(vystup, open(VYSTUP, "w"), ensure_ascii=False, indent=0, sort_keys=True)
print("nalezeno", sum(1 for x in vystup.values() if x), "z", len(vystup))

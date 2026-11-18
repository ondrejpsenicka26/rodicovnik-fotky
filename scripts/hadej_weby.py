"""Hledá web školky mezi obvyklými tvary adres (ms<obec>.cz, ms.<obec>.cz …).

Web se přijme jen tehdy, když na něm (úvodní nebo kontaktní stránka) je IČO školky, její ulice,
nebo PSČ u obce, kde je jediná školka. Respektuje robots.txt, na každý web jde jeden až čtyři požadavky.
Vstup:  hledani/hadej-vstup.json  {slug: {kand: [domény], sub: [subdomény obce], ico, psc, ulice, psc_staci}}
Výstup: hledani/hadej.json        {slug: {web, shoda, final, titulek} | null}
"""
import html
import json
import os
import random
import re
import socket
import string
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor

UA = "RodicovnikBot/1.0 (+https://github.com/ondrejpsenicka26/rodicovnik-fotky; prehled skolek v CR)"
vstup = json.load(open("hledani/hadej-vstup.json"))
VYSTUP = "hledani/hadej.json"
vystup = json.load(open(VYSTUP)) if os.path.exists(VYSTUP) else {}
CIZI = re.compile(r"casino|kasino|sázk|poker|viagra|porn|escort|doména je na prodej|domain (is )?for sale|"
                  r"buy this domain|parkovan|parked|webhosting|tato doména|this domain", re.I)
KONTAKT = re.compile(r"kontakt|o-nas|o-skol|o-skolc|zakladni-udaje|zakladni-informace|informace|uvod|o-ms|skolka", re.I)
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


_wild = {}


def wildcard(dom):
    """Obec, jejíž DNS odpoví na jakoukoli subdoménu: subdomény pak nic neznamenají."""
    if dom not in _wild:
        _wild[dom] = existuje("".join(random.choices(string.ascii_lowercase, k=12)) + "." + dom)
    return _wild[dom]


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
    if len(ul) >= 5 and ul in t:
        return "ulice"
    if z.get("psc_staci") and z.get("psc") and re.sub(r"\D", "", z["psc"]) in cisla:
        return "psc"
    return ""


def zkus(host, z):
    for sch in ("https://", "http://"):
        web = sch + host + "/"
        try:
            if not smim(web):
                return None
            final, t = nacti(web)
            break
        except Exception:  # noqa: BLE001
            continue
    else:
        return None
    fh = urllib.parse.urlparse(final).netloc.lower().removeprefix("www.")
    if fh != host.removeprefix("www."):
        return None  # přesměrování jinam (obec, hosting, jiná škola): nebrat
    tit = re.search(r"<title[^>]*>(.*?)</title>", t, re.I | re.S)
    titulek = html.unescape(re.sub(r"\s+", " ", tit.group(1))).strip()[:120] if tit else ""
    text = text_z(t)
    if CIZI.search(titulek) or (len(text) < 3000 and CIZI.search(text)):
        return None
    s = shoda(text, z)
    if not s:
        odkazy = []
        for m in re.finditer(r'<a[^>]+href=["\']([^"\'#]+)["\']', t, re.I):
            u = urllib.parse.urljoin(final, html.unescape(m.group(1)))
            if urllib.parse.urlparse(u).netloc == urllib.parse.urlparse(final).netloc \
                    and KONTAKT.search(bez(urllib.parse.unquote(u))) and u not in odkazy:
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
    if not s:
        return None
    return {"web": final.split("#")[0], "shoda": s, "final": final, "titulek": titulek}


def hledej(item):
    slug, z = item
    hosty = [h for h in z["kand"] if existuje(h) or existuje("www." + h)]
    for h in z["sub"]:
        dom = h.split(".", 1)[1]
        if existuje(h) and not wildcard(dom):
            hosty.append(h)
    for h in hosty:
        if not existuje(h):
            h = "www." + h
        v = zkus(h, z)
        if v:
            v["host"] = h
            return slug, v
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

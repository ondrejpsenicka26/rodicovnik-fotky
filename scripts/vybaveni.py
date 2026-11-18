"""Vybavení a služby školek z jejich webů: sbírá úryvky textu kolem klíčových slov.

Vstup:  vybaveni/vstup.json        {slug: {web, nazev, obec}}
Výstup: vybaveni/cast-{CAST}.json  {slug: {"stranky": [url…], "kontext": {klic: [[url, úryvek]…]}, "chyba": str}}

Na každý web jde úvodní stránka a nejvýš 8 podstránek o školce (o nás, prostory, strava,
aktivity…), vždy jen na stejném webu (a pod stejnou cestou, když je web podstránkou obce).
Řídí se robots.txt, mezi žádostmi na stejný web čeká. Vyhodnocení dělá až import:
tady se jen uloží úryvky, aby šla pravidla zpřesnit bez nového procházení.
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
CAST, CASTI = int(os.environ.get("CAST", "0")), int(os.environ.get("CASTI", "1"))
_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [x for x in _gai(*a, **k) if x[0] == socket.AF_INET] or _gai(*a, **k)

# podstránky, které o školce obvykle něco říkají (v adrese nebo v textu odkazu, bez diakritiky)
PODSTRANKY = re.compile(
    r"o-nas|o-skol|o-ms|o-mater|o-detsk|o-skupin|nase-skol|skolka|materska|vybaveni|prostor|zahrad|trid|"
    r"strav|jidel|kuchyn|aktivit|krouzk|nadstandard|program|koncepc|charakteristik|zakladni-informace|"
    r"zakladni-udaje|informace|provoz|adaptac|plavani|logoped|denni-rezim|rezim-dne|co-nabizime|nabizime|"
    r"sluzby|cenik|skolne|o-nas|nase-zarizeni|zarizeni|budova|areal",
    re.I,
)
VYNECH = re.compile(r"\.(pdf|docx?|xlsx?|jpe?g|png|gif|zip|rar|mp4|webp)(\?|$)|mailto:|tel:|javascript:|/wp-admin|"
                    r"login|prihlas|kalendar|fotogaleri|galerie|aktualit|novink|archiv|/tag/|/category/|\?", re.I)
# široký výběr slov (bez diakritiky); přesná pravidla se ladí až při importu
KLICE = {
    "zahrad": r"zahrad", "kuchyn": r"kuchyn|varime|vyvarovn|jidel", "bio": r"\bbio", "logoped": r"logoped",
    "plav": r"plav|bazen", "vylet": r"vylet|skol[ay] v prirode", "bezbar": r"bezbarier|imobil|vytah",
    "parkov": r"parkov", "adaptac": r"adaptac", "celorocni": r"celorocn|prazdnin|letni provoz|preruseni provozu",
    "jurta": r"jurt|maringot", "solna": r"soln[aeyou] (jeskyn|mistnost|komor)", "sauna": r"saun",
    "keramika": r"keramik", "anglictina": r"anglic|english", "krouzky": r"krouzk", "telocvicna": r"telocvic",
    "lyze": r"lyz", "brusle": r"brusl",
}
KLICE_RE = {k: re.compile(v) for k, v in KLICE.items()}

_robots = {}


def bez(s):
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


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
    try:
        r = urllib.request.urlopen(req, timeout=30)
    except urllib.error.URLError as e:
        if "CERTIFICATE" in str(e) and u.startswith("https://"):
            r = urllib.request.urlopen(urllib.request.Request("http://" + u[8:], headers=req.headers), timeout=30)
        else:
            raise
    with r:
        final = r.geturl()
        ct = r.headers.get("Content-Type", "")
        if "html" not in ct.lower() and ct:
            return final, ""
        data = r.read(2_000_000)
    m = re.search(r"charset=([\w-]+)", ct, re.I) or re.search(rb'charset=["\']?([\w-]+)', data[:3000], re.I)
    enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
    try:
        return final, data.decode(enc, errors="replace")
    except LookupError:
        return final, data.decode("utf-8", errors="replace")


def text_z(t):
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>|<!--.*?-->", " ", t, flags=re.I | re.S)
    t = re.sub(r"<(br|/p|/div|/li|/h\d|/tr|/td)[^>]*>", " . ", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


def odkazy(final, t, prefix):
    host = urllib.parse.urlparse(final).netloc.lower().removeprefix("www.")
    out = []
    for m in re.finditer(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', t, re.I | re.S):
        u = urllib.parse.urljoin(final, html.unescape(m.group(1).strip()))
        p = urllib.parse.urlparse(u)
        if p.scheme not in ("http", "https") or p.netloc.lower().removeprefix("www.") != host:
            continue
        if prefix and not p.path.lower().startswith(prefix):
            continue
        if VYNECH.search(u):
            continue
        popis = bez(re.sub(r"<[^>]+>", " ", m.group(2)))
        if PODSTRANKY.search(bez(urllib.parse.unquote(p.path))) or PODSTRANKY.search(popis.replace(" ", "-")):
            u = u.split("#")[0]
            if u not in out and u.rstrip("/") != final.rstrip("/"):
                out.append(u)
    return out


def kontexty(url, text, vysl):
    b = bez(text)
    # bez() nemění délku znaků jen u složených znaků; úryvky bereme z textu bez diakritiky i s ní
    shoda_delek = len(b) == len(text)
    for k, rx in KLICE_RE.items():
        seznam = vysl.setdefault(k, [])
        for m in rx.finditer(b):
            if len(seznam) >= 6:
                break
            a, z = max(0, m.start() - 160), min(len(b), m.end() + 160)
            uryvek = (text if shoda_delek else b)[a:z].strip()
            if not any(uryvek[40:120] in x[1] for x in seznam):
                seznam.append([url, uryvek])


def zpracuj(item):
    slug, z = item
    out = {"stranky": [], "kontext": {}, "chyba": ""}
    try:
        web = z["web"].strip()
        if not smim(web):
            out["chyba"] = "robots"
            return slug, out
        final, t = nacti(web)
        out["stranky"].append(final)
        kontexty(final, text_z(t), out["kontext"])
        cesta = urllib.parse.urlparse(web).path.lower().rstrip("/")
        prefix = cesta + "/" if cesta and cesta.count("/") >= 1 and not re.search(r"\.(html?|php|aspx?)$", cesta) else ""
        for u in odkazy(final, t, prefix)[:8]:
            if not smim(u):
                continue
            time.sleep(0.6)
            try:
                f2, t2 = nacti(u)
            except Exception:  # noqa: BLE001
                continue
            out["stranky"].append(f2)
            kontexty(f2, text_z(t2), out["kontext"])
        out["kontext"] = {k: v for k, v in out["kontext"].items() if v}
    except Exception as e:  # noqa: BLE001
        out["chyba"] = str(e)[:120]
    return slug, out


vstup = json.load(open("vybaveni/vstup.json"))
cil = f"vybaveni/cast-{CAST}.json"
vysl = json.load(open(cil)) if os.path.exists(cil) else {}
klice = sorted(vstup)
polozky = [(s, vstup[s]) for i, s in enumerate(klice) if i % CASTI == CAST and s not in vysl]
print(len(polozky), "webů ke zpracování")
start = time.time()
with ThreadPoolExecutor(12) as ex:
    for i, (slug, o) in enumerate(ex.map(zpracuj, polozky)):
        vysl[slug] = o
        if i % 100 == 99:
            json.dump(vysl, open(cil, "w"), ensure_ascii=False, indent=0, sort_keys=True)
            print(i + 1, f"{time.time() - start:.0f} s", flush=True)
json.dump(vysl, open(cil, "w"), ensure_ascii=False, indent=0, sort_keys=True)
print("hotovo", len(vysl), "chyb", sum(1 for v in vysl.values() if v["chyba"]))

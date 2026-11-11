"""Fotky budov, tříd a zahrad mateřských škol a dětských skupin z jejich webů (se svolením).

Zdroje: web školky (úvod, stránky o prostorách, galerie a jejich alba) a fotogalerie,
na které školka ze svého webu odkazuje (Rajče, Zonerama, Flickr). Alba bereme jen podle
názvu typu zahrada / prostory / třídy / rekonstrukce / den otevřených dveří – akce s dětmi ne.

1. CLIP roztřídí fotky na budovu, interiér, zahradu / les, umývárnu a ostatní.
2. YOLO vyřadí KAŽDOU fotku, na které je člověk (i malý v pozadí).
3. Duplicity a fotky z webů víc školek (šablony, fotobanky) se vyřadí.
4. Na školku max. 4 fotky, WebP 1200 px bez EXIF + náhled 560 px.

Weby se zpracovávají po dávkách (stáhnout → roztřídit → uložit → smazat dočasné soubory),
takže běh jde kdykoli přerušit a další běh pokračuje tam, kde skončil.

Proměnné prostředí:
  SHARD, SHARDS   tento běh zpracuje weby, kde crc32(web) % SHARDS == SHARD
  LIMIT_MIN       po kolika minutách přestat brát další dávky (výchozí 300)
Vstup:  cile.json  {web: {"slug": …, "jen_ms": bool}}
Výstup: fotky/<slug>/<n>.webp (+ 1-nahled.webp), manifest/<shard>.json, hotovo/<shard>.json
"""

import hashlib
import html
import io
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

from PIL import Image, ImageOps

SHARD = int(os.environ.get("SHARD", "0"))
SHARDS = int(os.environ.get("SHARDS", "1"))
LIMIT_S = float(os.environ.get("LIMIT_MIN", "300")) * 60
START = time.time()
UA = "RodicovnikBot/1.0 (+https://github.com/ondrejpsenicka26/rodicovnik-fotky; prehled skolek v CR)"
MAX_STRANEK = 8  # 1. úroveň webu
MAX_ALB = 8  # alba na webu školky
MAX_EXT_ALB = 4  # alba na Rajčeti / Zoneramě
MAX_KANDIDATU = 45
MAX_FOTEK = 4
DAVKA = 24  # webů na dávku
LOG = []
lock = threading.Lock()
Image.MAX_IMAGE_PIXELS = 60_000_000
TMP = f"/tmp/fotky-{SHARD}-kandidati"
os.makedirs(TMP, exist_ok=True)
GALERIE = ("rajce.idnes.cz", "rajce.net", "zonerama.com", "flickr.com", "photos.app.goo.gl", "photos.google.com")


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    with lock:
        print(line, flush=True)
        LOG.append(line)


def bez_diakritiky(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


# ---------- slušné stahování: robots.txt a rozestupy na hosta ----------
_robots, _robots_lock = {}, threading.Lock()
_posledni, _host_lock = {}, threading.Lock()


def smim(u):
    p = urllib.parse.urlparse(u)
    klic = f"{p.scheme}://{p.netloc}"
    with _robots_lock:
        rp = _robots.get(klic, "?")
    if rp == "?":
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(klic + "/robots.txt", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                rp.parse(r.read(300_000).decode("utf-8", "replace").splitlines())
        except Exception:  # noqa: BLE001
            rp = None
        with _robots_lock:
            _robots[klic] = rp
    try:
        return rp is None or rp.can_fetch(UA, u)
    except Exception:  # noqa: BLE001
        return True


def pockej(host, rozestup=0.4):
    while True:
        with _host_lock:
            t = time.time()
            if t - _posledni.get(host, 0) >= rozestup:
                _posledni[host] = t
                return
        time.sleep(rozestup / 4)


def stahni(url, max_bytes=8_000_000, accept="*/*"):
    pockej(urllib.parse.urlparse(url).netloc)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "cs"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.geturl(), r.headers.get("Content-Type", ""), r.read(max_bytes)


def dekoduj(data, ctype):
    m = re.search(r"charset=([\w-]+)", ctype or "", re.I) or re.search(rb'charset=["\']?([\w-]+)', data[:3000], re.I)
    enc = (m.group(1).decode() if isinstance(m.group(1), bytes) else m.group(1)) if m else "utf-8"
    try:
        return data.decode(enc, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


# ---------- výběr stránek a alb ----------
STRANKY = (
    "galeri", "foto", "o-nas", "o-skolce", "o-skolc", "o-ms", "prostor", "prostred", "zahrad", "trid", "interier",
    "budov", "areal", "prohlidk", "vybaven", "about", "gallery", "nase-skolka", "skolka", "zazemi", "herna", "album",
)
NE_STRANKY = ("aktualit", "novink", "blog", "kontakt", "jidelnic", "dokument", "gdpr", "cookie", "kariera")
ALBA_ANO = (
    "zahrad", "prostor", "trid", "interier", "budov", "rekonstruk", "oprav", "nova-", "nove-", "novy-", "herna",
    "lozni", "jideln", "umyvar", "satn", "otevren", "dod", "areal", "hrist", "prohlid", "vybaven", "stavb",
    "kolaudac", "nase-skolka", "skolka", "skolicka", "pavilon", "terasa", "pergol", "jurta", "zazemi", "ms-",
    "materska", "garden", "classroom", "premises", "facilit", "interior", "building", "playground",
)
ALBA_NE = (
    "karneval", "besidk", "vylet", "divadl", "maskarn", "vanoc", "mikulas", "carodej", "olympi", "plavan", "foceni",
    "portret", "rozlouc", "narozen", "masopust", "velikon", "den-deti", "den-matek", "koncert", "sportovni", "vystup",
    "kouzelnik", "slavnost", "oslav", "tabor", "skolka-v-prirode", "lyzar", "bruslen", "zapis",
)
NE_OBRAZKY = (
    "logo", "icon", "ikon", "favicon", "erb", "znak", "qr", "mapa", "map_", "/map", "facebook", "instagram",
    "youtube", "twitter", "button", "tlacitk", "arrow", "sipk", "sponzor", "partner", "eu-", "_eu", "msmt", "opvvv",
    "esf", "op-", "banner-eu", "podpis", "razitk", "avatar", "emoji", "loader", "spinner", "placeholder", "pixel",
    "jidelnicek", "plakat", "letak", "pozvank", "diplom", "certifik",
)
MS_KONTEXT = re.compile(r"(?<![a-z])(ms|ms-|materska|skolk|skolick|predskol)", re.I)


def cesta_url(href):
    p = urllib.parse.urlparse(href)
    return bez_diakritiky(urllib.parse.unquote(p.path + "?" + p.query))


def skore_stranky(href, text):
    h = cesta_url(href)
    t = bez_diakritiky(text)
    if any(k in h for k in NE_STRANKY):
        return 0
    s = 0
    for k in STRANKY:
        if k in h or k.replace("-", " ") in t:
            s += 5 if k in ("galeri", "foto", "prostor", "zahrad", "trid", "interier", "budov", "zazemi") else 2
    return s


def skore_alba(href, text):
    h = cesta_url(href).replace("_", "-").replace(" ", "-")
    t = bez_diakritiky(text).replace(" ", "-")
    if any(k in h or k in t for k in ALBA_NE):
        return 0
    return sum(3 for k in ALBA_ANO if k in h or k in t)


class Stranka(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.imgs = [], []
        self._a, self._txt = None, []
        self.title, self._in_title = "", False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        styl = a.get("style") or ""
        for m in re.finditer(r"url\(['\"]?([^'\")]+)", styl):
            self.imgs.append((m.group(1), "", 0))
        if a.get("data-bg"):
            self.imgs.append((a["data-bg"], "", 0))
        if tag == "title":
            self._in_title = True
        if tag == "a":
            self._a = a.get("href")
            self._txt = [a.get("title") or ""]
            if self._a and re.search(r"\.(jpe?g|png|webp)(\?|$)", self._a, re.I):
                self.imgs.append((self._a, a.get("title") or "", 2))  # odkaz na velkou fotku (lightbox)
        if tag == "img":
            src = a.get("data-src") or a.get("data-lazy-src") or a.get("data-original") or a.get("src")
            srcset = a.get("data-srcset") or a.get("srcset") or ""
            nej, nej_w = None, 0
            for kus in srcset.split(","):
                p = kus.strip().split()
                if len(p) == 2 and p[1].endswith("w") and p[1][:-1].isdigit() and int(p[1][:-1]) > nej_w:
                    nej, nej_w = p[0], int(p[1][:-1])
            if nej:
                self.imgs.append((nej, a.get("alt") or "", 1))
            if src:
                self.imgs.append((src, a.get("alt") or "", 0))
            if self._a is not None:
                self._txt.append(a.get("alt") or "")
        if tag == "meta" and a.get("property") in ("og:image", "og:image:url") and a.get("content"):
            self.imgs.append((a["content"], "og", 1))

    def handle_endtag(self, tag):
        if tag == "a" and self._a:
            self.links.append((self._a, " ".join(self._txt).strip()))
            self._a = None
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._a is not None:
            self._txt.append(data.strip())
        if self._in_title:
            self.title += data


def varianty_url(u):
    """Zkusí nejdřív originál (WordPress -300x200, Wix /v1/fill…, Rajče /thumb/), pak zadanou adresu."""
    out = []
    wp = re.sub(r"-\d{2,4}x\d{2,4}(?=\.(jpe?g|png|webp)$)", "", u, flags=re.I)
    if wp != u:
        out.append(wp)
    wix = re.sub(r"(static\.wixstatic\.com/media/[^/]+)/v1/.*$", r"\1", u)
    if wix != u:
        out.append(wix)
    if "rajce" in u and "/thumb/" in u:
        out.append(u.replace("/thumb/", "/images/"))
    out.append(u)
    return out


def nacti_stranku(u, pokusy=1):
    if not smim(u):
        return None
    for i in range(pokusy + 1):
        try:
            f, c, d = stahni(u, 3_000_000, "text/html")
            break
        except Exception:  # noqa: BLE001
            if i == pokusy:
                return None
            time.sleep(3)
    if "html" not in c or not smim(f):
        return None
    doc = dekoduj(d, c)
    p = Stranka()
    p.feed(doc)
    if "photos.google.com" in f:  # sdílené album Google Fotky: fotky jsou v datech stránky
        for m in sorted(set(re.findall(r"https://lh3\.googleusercontent\.com/pw/[\w-]{30,}", doc)))[:60]:
            p.imgs.append((m + "=w1600-h1600", "", 1))
    return f, p


def odkazy(final, p, jen_host=None):
    host = urllib.parse.urlparse(final).netloc.replace("www.", "")
    for href, txt in p.links:
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        u = urllib.parse.urljoin(final, href).split("#")[0]
        pu = urllib.parse.urlparse(u)
        if pu.scheme not in ("http", "https"):
            continue
        if re.search(r"\.(jpe?g|png|gif|webp|pdf|docx?|zip|mp4)$", pu.path, re.I):
            continue
        h2 = pu.netloc.replace("www.", "")
        if jen_host == "stejny" and h2 != host:
            continue
        yield u, txt, h2


def projdi(origin, slug, jen_ms=False):
    stranky = []  # (url, title, skore)
    prvni = nacti_stranku(origin + "/", pokusy=2)
    if not prvni:
        log(f"{origin}: nedostupné nebo zakázané")
        return []
    final, p0 = prvni
    stranky.append((final, p0, 1))
    videno = {final}
    # 1. úroveň: stránky o školce a galerie
    kand = {}
    externi = {}
    for u, txt, h2 in odkazy(final, p0):
        if any(g in h2 for g in GALERIE):
            externi[u] = externi.get(u, "") + " " + txt
            continue
        if h2 != urllib.parse.urlparse(final).netloc.replace("www.", ""):
            continue
        sk = skore_stranky(u, txt)
        if sk > 0:
            kand[u] = max(kand.get(u, 0), sk)
    for u, sk in sorted(kand.items(), key=lambda x: -x[1])[:MAX_STRANEK]:
        if u in videno:
            continue
        videno.add(u)
        r = nacti_stranku(u)
        if r:
            stranky.append((r[0], r[1], 1 + skore_alba(u, r[1].title)))
    # 2. úroveň: alba v galeriích webu (jen podle názvu typu zahrada / prostory / třídy)
    alba = {}
    for url, p, _ in list(stranky):
        for u, txt, h2 in odkazy(url, p):
            if any(g in h2 for g in GALERIE):
                externi[u] = externi.get(u, "") + " " + txt
                continue
            if h2 != urllib.parse.urlparse(final).netloc.replace("www.", "") or u in videno:
                continue
            sk = skore_alba(u, txt)
            if sk > 0 and re.search(r"galeri|foto|album|gallery", bez_diakritiky(url + u)):
                alba[u] = max(alba.get(u, 0), sk)
    for u, sk in sorted(alba.items(), key=lambda x: -x[1])[:MAX_ALB]:
        videno.add(u)
        r = nacti_stranku(u)
        if r:
            stranky.append((r[0], r[1], 3 + sk))
    # externí galerie, na které školka sama odkazuje: alba podle názvu; u kořene galerie (profil
    # na Rajčeti / Zoneramě) projdeme seznam alb
    ext_alba, koreny = {}, []
    for u, txt in externi.items():
        cesta = urllib.parse.urlparse(u).path.strip("/")
        if not cesta and "goo.gl" not in u:
            koreny.append(u)
            continue
        sk = skore_alba(u, txt)
        if sk > 0:
            ext_alba[u] = max(ext_alba.get(u, 0), sk)
    for g in koreny[:2]:
        r = nacti_stranku(g)
        if not r:
            continue
        gf, gp = r
        for u, txt, h2 in odkazy(gf, gp, "stejny"):
            sk = skore_alba(u, txt)
            if sk > 0:
                ext_alba[u] = max(ext_alba.get(u, 0), sk)
    for u, sk in sorted(ext_alba.items(), key=lambda x: -x[1])[:MAX_EXT_ALB]:
        if u in videno:
            continue
        videno.add(u)
        r = nacti_stranku(u)
        if r:
            stranky.append((r[0], r[1], 3 + sk))
    # obrázky
    obrazky = {}
    for url, p, sk_str in stranky:
        kontext = bez_diakritiky(urllib.parse.unquote(url) + " " + p.title)
        for src, alt, prio in p.imgs:
            src = html.unescape(src.strip())
            if not src or src.startswith("data:"):
                continue
            u = urllib.parse.urljoin(url, src)
            if not u.startswith("http"):
                continue
            nizko = bez_diakritiky(urllib.parse.unquote(u))
            if re.search(r"\.(svg|gif|ico)(\?|$)", nizko) or any(k in nizko for k in NE_OBRAZKY):
                continue
            if any(k in bez_diakritiky(alt) for k in ("logo", "mapa", "plakat", "letak")):
                continue
            ms = bool(MS_KONTEXT.search(kontext + " " + bez_diakritiky(alt)))
            if jen_ms and not ms:
                continue  # u spojených ZŠ a MŠ jen fotky z části webu o školce
            klic = (sk_str, prio)
            stary = obrazky.get(u)
            if not stary or klic > stary[0]:
                obrazky[u] = (klic, alt, url, ms)
    serazene = sorted(obrazky.items(), key=lambda x: x[1][0], reverse=True)[:MAX_KANDIDATU]
    vysledek, hashe = [], set()
    for u, (_, alt, stranka, ms) in serazene:
        for v in varianty_url(u):
            if not smim(v):
                continue
            try:
                _, ct, b = stahni(v, 12_000_000, "image/*")
            except Exception:  # noqa: BLE001
                continue
            if len(b) < 25_000:
                break  # malý soubor = ikona / náhled
            h = hashlib.sha1(b).hexdigest()
            if h in hashe:
                break
            try:
                im = Image.open(io.BytesIO(b))
                im.load()
                im = ImageOps.exif_transpose(im).convert("RGB")
            except Exception:  # noqa: BLE001
                continue
            w, hgt = im.size
            if w < 700 or hgt < 450 or not (0.55 <= w / hgt <= 2.4):
                break
            hashe.add(h)
            im.thumbnail((1400, 1400))
            cesta = f"{TMP}/{slug}-{len(vysledek)}.jpg"
            im.save(cesta, "JPEG", quality=88)
            vysledek.append({"cesta": cesta, "url": v, "stranka": stranka, "alt": alt[:120], "w0": w, "h0": hgt,
                             "ms": ms})
            break
    hosty = sorted({urllib.parse.urlparse(u).netloc for u in externi})[:3]
    log(f"{origin}: {len(stranky)} stránek (galerie mimo web: {len(externi)} {hosty}, alb: {len(ext_alba)}), {len(obrazky)} obrázků, "
        f"{len(vysledek)} fotek ke kontrole")
    return vysledek


# ---------- třídění ----------
KEEP = {
    "budova": [
        "a photo of a kindergarten building from outside",
        "a photo of a school building facade",
        "a photo of a villa or house with a garden, seen from the street",
    ],
    "interier": [
        "a photo of an empty kindergarten classroom with small tables and chairs",
        "a photo of an empty playroom with toys and shelves",
        "a photo of a nap room with small children's beds",
        "a photo of a bright room with a carpet and wooden toys",
    ],
    "hygiena": [
        "a photo of a children's washroom with small sinks and toilets",
    ],
    "zahrada": [
        "a photo of a kindergarten garden with playground equipment",
        "a photo of an empty outdoor playground with a sandbox and slide",
        "a photo of a garden with trees, lawn and a wooden playhouse",
    ],
    "les": [
        "a photo of a yurt, tipi or wooden hut in a meadow or forest",
        "a photo of a forest kindergarten base with a wooden shelter",
    ],
}
REJECT = [
    "a photo of children", "a photo of a child", "a photo of people", "a group photo of kids and teachers",
    "a children's drawing", "a painting made by children", "a handmade craft made by kids",
    "a poster or flyer with text", "a document with text", "a menu or timetable", "a logo", "an illustration or clipart",
    "a photo of food on a plate", "a map", "a photo of an animal", "a collage of several photos", "a screenshot",
    "a photo of a forest without any building", "a photo of a city street", "a photo of a theatre performance",
    "a close-up photo of toys", "a photo of a book or a toy on a table",
]


def dhash(im, n=8):
    g = im.convert("L").resize((n + 1, n), Image.LANCZOS)
    px = list(g.tobytes())
    bity = 0
    for r in range(n):
        for c in range(n):
            bity = (bity << 1) | (px[r * (n + 1) + c] > px[r * (n + 1) + c + 1])
    return bity


_MODELY = {}


def modely():
    if not _MODELY:
        import open_clip
        import torch
        from ultralytics import YOLO

        torch.set_num_threads(os.cpu_count() or 4)
        model, _, pre = open_clip.create_model_and_transforms("ViT-B-32", pretrained="laion2b_s34b_b79k")
        tok = open_clip.get_tokenizer("ViT-B-32")
        model.eval()
        popisy, skupiny = [], []
        for k, v in KEEP.items():
            popisy += v
            skupiny += [k] * len(v)
        popisy += REJECT
        skupiny += ["ne"] * len(REJECT)
        with torch.no_grad():
            tf = model.encode_text(tok(popisy))
            tf /= tf.norm(dim=-1, keepdim=True)
        _MODELY.update(model=model, pre=pre, tf=tf, skupiny=skupiny, yolo=YOLO("yolo11s.pt"))
    return _MODELY


def roztrid(polozky):
    import torch

    m = modely()
    model, pre, tf, skupiny, yolo = m["model"], m["pre"], m["tf"], m["skupiny"], m["yolo"]
    for zacatek in range(0, len(polozky), 32):
        davka = polozky[zacatek : zacatek + 32]
        obrazky = [Image.open(p["cesta"]).convert("RGB") for p in davka]
        with torch.no_grad():
            f = model.encode_image(torch.stack([pre(i) for i in obrazky]))
            f /= f.norm(dim=-1, keepdim=True)
            pr = (100 * f @ tf.T).softmax(dim=-1).tolist()
        for p, img, radek in zip(davka, obrazky, pr):
            p["dh"] = dhash(img)
            souc = {}
            for s, x in zip(skupiny, radek):
                souc[s] = souc.get(s, 0) + x
            typ = max((k for k in souc if k != "ne"), key=lambda k: souc[k])
            p["typ"], p["jistota"] = typ, round(souc[typ], 3)
            p["lide"] = 0
            if souc[typ] >= 0.55:
                r = yolo.predict(img, imgsz=1024, conf=0.15, classes=[0], verbose=False)[0]
                p["lide"] = len(r.boxes)
        if zacatek % 640 == 0:
            log(f"tříděno {zacatek}/{len(polozky)}")


def nacti_json(cesta, vychozi):
    try:
        return json.load(open(cesta))
    except (FileNotFoundError, json.JSONDecodeError):
        return vychozi


def barva_a_lqip(im, s_obrazkem):
    import base64

    r, g, b = im.resize((1, 1), Image.LANCZOS).getpixel((0, 0))
    out = {"barva": f"#{r:02x}{g:02x}{b:02x}"}
    if s_obrazkem:
        w = 20
        h = max(1, round(w * im.height / im.width))
        buf = io.BytesIO()
        im.resize((w, h), Image.LANCZOS).save(buf, "JPEG", quality=45, optimize=True)
        out["lqip"] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return out


def vyber_a_uloz(vse, polozky, manifest, statistika):
    podle_hashe = {}
    for p in polozky:
        podle_hashe.setdefault(p["dh"], set()).add(p["slug"])
    for s in vse:
        mozne = []
        for p in (x for x in polozky if x["slug"] == s):
            if p["jistota"] < 0.55:
                statistika["jine"] += 1
            elif p["lide"] > 0:
                statistika["lide"] += 1
            elif len(podle_hashe[p["dh"]]) > 1:
                statistika["sablona"] += 1
            else:
                mozne.append(p)
        mozne.sort(key=lambda p: -(p["jistota"] + min(p["w0"], 2400) / 8000))
        vybrane = []
        for p in mozne:
            if any(bin(p["dh"] ^ q["dh"]).count("1") <= 10 for q in vybrane):
                continue
            vybrane.append(p)
        # pestrý výběr: budova, třída, zahrada, …; umývárna nakonec
        limity = {"budova": 2, "interier": 2, "zahrada": 2, "les": 2, "hygiena": 1}
        fronty = {t: [p for p in vybrane if p["typ"] == t][:lim] for t, lim in limity.items()}
        vyber = []
        while len(vyber) < MAX_FOTEK and any(fronty[t] for t in ("budova", "interier", "zahrada", "les")):
            for t in ("budova", "interier", "zahrada", "les"):
                if fronty[t] and len(vyber) < MAX_FOTEK:
                    vyber.append(fronty[t].pop(0))
        vyber += fronty["hygiena"][: MAX_FOTEK - len(vyber)]
        if not vyber:
            continue
        os.makedirs(f"fotky/{s}", exist_ok=True)
        zaznamy = []
        for n, p in enumerate(vyber, 1):
            src = Image.open(p["cesta"]).convert("RGB")
            im = src.copy()
            im.thumbnail((1200, 1200))
            im.save(f"fotky/{s}/{n}.webp", "WEBP", quality=70, method=6)
            z = {"soubor": f"{n}.webp", "w": im.size[0], "h": im.size[1], "typ": p["typ"], "jistota": p["jistota"],
                 "url": p["url"], "stranka": p["stranka"], "alt": p["alt"], "ms": p["ms"], "dh": f"{p['dh']:016x}"}
            if n == 1:
                t = src.copy()
                t.thumbnail((560, 560))
                t.save(f"fotky/{s}/1-nahled.webp", "WEBP", quality=68, method=6)
                z["nahled"] = "1-nahled.webp"
            z.update(barva_a_lqip(im, n == 1))
            zaznamy.append(z)
            statistika["ok"] += 1
        manifest[s] = zaznamy


def main():
    import shutil
    import zlib

    cile = nacti_json("cile.json", {})
    only = sys.argv[1:]
    if only:
        cile = {o: c for o, c in cile.items() if any(x in o for x in only)}
    cile = {o: c for o, c in cile.items() if zlib.crc32(o.encode()) % SHARDS == SHARD}
    os.makedirs("manifest", exist_ok=True)
    os.makedirs("hotovo", exist_ok=True)
    manifest = nacti_json(f"manifest/{SHARD}.json", {})
    hotovo = set(nacti_json(f"hotovo/{SHARD}.json", []))
    zbyva = [(o, c) for o, c in sorted(cile.items()) if o not in hotovo]
    log(f"Část {SHARD + 1}/{SHARDS}: webů {len(cile)}, hotovo {len(hotovo)}, zbývá {len(zbyva)}")
    statistika = {"ok": 0, "lide": 0, "jine": 0, "sablona": 0}

    def uloz():
        json.dump(manifest, open(f"manifest/{SHARD}.json", "w"), ensure_ascii=False, indent=0, sort_keys=True)
        json.dump(sorted(hotovo), open(f"hotovo/{SHARD}.json", "w"), ensure_ascii=False, indent=0)

    for zacatek in range(0, len(zbyva), DAVKA):
        if time.time() - START > LIMIT_S:
            log("Časový limit – zbytek v dalším běhu")
            break
        davka = zbyva[zacatek : zacatek + DAVKA]
        vse = {}
        with ThreadPoolExecutor(12) as ex:
            for s, r in ex.map(lambda kv: (kv[1]["slug"], projdi(kv[0], kv[1]["slug"], kv[1].get("jen_ms", False))), davka):
                vse[s] = r
        polozky = [p | {"slug": s} for s, r in vse.items() for p in r]
        if polozky:
            roztrid(polozky)
            vyber_a_uloz(vse, polozky, manifest, statistika)
        hotovo.update(o for o, _ in davka)
        uloz()
        shutil.rmtree(TMP, ignore_errors=True)
        os.makedirs(TMP, exist_ok=True)
        log(f"Dávka {zacatek // DAVKA + 1}: hotovo {len(hotovo)}/{len(cile)}, se fotkami {len(manifest)}; {statistika}")
    uloz()
    log(f"Konec: školek s fotkami {len(manifest)}; {statistika}")
    os.makedirs("logy", exist_ok=True)
    open(f"logy/{SHARD}.txt", "w").write("\n".join(LOG[-3000:]))


if __name__ == "__main__":
    main()

"""Fakta z vlastních webů Kit (Kita Finder): provoz, věk přijetí, počet míst, zaměření,
vybavení, kontakt (telefon, e-mail) a u soukromých Kit cena.

Bereme jen fakta (ne texty ani fotky) a jen z webů, které patří jedné Kitě – web sdílený
více Kitami (portál města, nosiče) přeskakujeme, aby se údaje nepletly.
Výstup: de/data/weby.json  {osm id: {url, datum, provoz, vek, kapacita, kat, vlastnosti, tel, mail, cena}}
"""
import json, os, re, time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from html import unescape
from urllib.parse import urljoin, urlparse

import requests

OUT = os.path.join(os.path.dirname(__file__), "..", "data")
UA = {"User-Agent": "Mozilla/5.0 (compatible; KitaFinderBot/1.0; +https://github.com/ondrejpsenicka26/rodicovnik-fotky)",
      "Accept-Language": "de-DE,de;q=0.9"}
PODSTRANKY = re.compile(r"öffnungszeit|oeffnungszeit|zeiten|konzept|paedagog|pädagog|über-uns|ueber-uns|uber-uns|"
                        r"wir-ueber|kontakt|aufnahme|anmeldung|kosten|beitr|gebühr|gebuehr|einrichtung|kita|haus|"
                        r"tagesablauf|team|leitbild|gruppen|angebot", re.I)
CAS = r"(\d{1,2})[:.](\d{2})"
R_PROVOZ = re.compile(CAS + r"\s*(?:uhr)?\s*(?:-|–|—|bis)\s*" + CAS + r"\s*uhr", re.I)
R_KLIC_PROVOZ = re.compile(r"öffnungszeit|oeffnungszeit|geöffnet|geoeffnet|betreuungszeit|öffnen|montag|mo\.?\s*-\s*fr", re.I)
R_VEK = [
    (re.compile(r"ab\s+(?:dem\s+)?(\d{1,2})\.?\s*(?:lebens)?monat", re.I), 1),
    (re.compile(r"ab\s+(\d)\s*(?:jahr|jähr|jaehr)", re.I), 12),
    (re.compile(r"ab\s+dem\s+(\d)\.\s*lebensjahr", re.I), 12),
    (re.compile(r"(?:von|im alter von)\s+(\d)\s*(?:bis|-|–)\s*(?:6|sechs|7|zum schuleintritt)", re.I), 12),
    (re.compile(r"(\d{1,2})\s*monate?n?\s*(?:bis|-|–)\s*(?:zum\s+)?(?:schuleintritt|6\s*jahr)", re.I), 1),
]
R_KAP = [
    re.compile(r"(?:bis zu|insgesamt|für|platz für|betreuen|betreut)\s+(\d{2,3})\s+kinder", re.I),
    re.compile(r"(\d{2,3})\s+(?:betreuungs)?plätze", re.I),
    re.compile(r"(\d{2,3})\s+kinder\s+(?:im alter|zwischen|von|in)", re.I),
]
KAT = {
    "montessori": re.compile(r"montessori", re.I),
    "waldorf": re.compile(r"waldorf", re.I),
    "wald": re.compile(r"waldkindergarten|waldgruppe|waldkita|naturkindergarten|naturgruppe", re.I),
    "bilingual": re.compile(r"bilingual|zweisprachig|immersion|english[- ]speaking|deutsch[- ]englisch", re.I),
    "kneipp": re.compile(r"kneipp", re.I),
    "bewegung": re.compile(r"bewegungskita|bewegungskindergarten|anerkannter bewegungs|bewegte kita", re.I),
    "integrativ": re.compile(r"integrativ|inklusiv|inklusion|heilpädagog|heilpaedagog", re.I),
}
VLASTNOSTI = {
    "zahrada": re.compile(r"garten|außengelände|aussengelaende|außenbereich", re.I),
    "vlastni-kuchyne": re.compile(r"eigene[nr]? küche|eigene[nr]? kueche|frisch gekocht|täglich frisch|selbst gekocht", re.I),
    "bio-strava": re.compile(r"\bbio[- ]?(?:essen|kost|qualität|lebensmittel|zutaten|zertifiziert)", re.I),
    "logoped": re.compile(r"logopäd|logopaed|sprachförder", re.I),
    "plavani": re.compile(r"schwimm", re.I),
    "telocvicna": re.compile(r"turnraum|turnhalle|bewegungsraum|mehrzweckraum", re.I),
    "anglictina": re.compile(r"englisch|english", re.I),
    "vylety": re.compile(r"ausflüge|ausfluege|exkursion", re.I),
    "adaptace-s-rodicem": re.compile(r"berliner (?:eingewöhnungs)?modell|münchener (?:eingewöhnungs)?modell|eingewöhnung", re.I),
}
R_CENA = re.compile(r"(\d{3,4}(?:[.,]\d{2})?)\s*(?:€|euro|eur)\s*(?:pro|im|/|monatlich|mtl)", re.I)
R_TEL = re.compile(r'href="tel:([+\d][\d\s/()+-]{6,})"', re.I)
R_MAIL = re.compile(r'href="mailto:([^"?]+@[^"?]+)"', re.I)


def text(html):
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    t = unescape(re.sub(r"<[^>]+>", " ", html))
    return re.sub(r"\s+", " ", t)


def stahni(url, s):
    try:
        r = s.get(url, timeout=15, allow_redirects=True)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and "html" in ct:
            return r.url, r.text[:600_000]
    except Exception:
        pass
    return None, None


def odkazy(base, html):
    out = []
    host = urlparse(base).netloc
    for h in re.findall(r'href="([^"#]+)"', html, re.I):
        u = urljoin(base, h)
        p = urlparse(u)
        if p.netloc != host or not p.scheme.startswith("http"):
            continue
        if re.search(r"\.(pdf|jpe?g|png|gif|zip|docx?)$", p.path, re.I):
            continue
        if PODSTRANKY.search(p.path):
            out.append(u.split("?")[0])
    return list(dict.fromkeys(out))[:5]


def fakta(txt, htmls):
    f = {}
    # provoz: časy blízko klíčových slov
    kandid = []
    for m in R_PROVOZ.finditer(txt):
        okoli = txt[max(0, m.start() - 220): m.start()]
        if not R_KLIC_PROVOZ.search(okoli):
            continue
        a, b = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
        if 5 * 60 + 30 <= a <= 10 * 60 and 11 * 60 <= b <= 20 * 60 and b - a >= 180:
            kandid.append((a, b))
    if kandid:
        a = min(x[0] for x in kandid)
        b = max(x[1] for x in kandid)
        f["provoz"] = f"{a // 60}:{a % 60:02d}-{b // 60}:{b % 60:02d}"
    # věk
    for rx, nasobek in R_VEK:
        m = rx.search(txt)
        if m:
            v = int(m.group(1)) * nasobek
            if 2 <= v <= 72:
                f["vek"] = v
                break
    # kapacita
    for rx in R_KAP:
        hodnoty = [int(x) for x in rx.findall(txt) if 8 <= int(x) <= 400]
        if hodnoty:
            f["kapacita"] = Counter(hodnoty).most_common(1)[0][0]
            break
    f["kat"] = sorted(k for k, rx in KAT.items() if len(rx.findall(txt)) >= (1 if k in ("montessori", "waldorf", "kneipp") else 2))
    f["vlastnosti"] = sorted(k for k, rx in VLASTNOSTI.items() if rx.search(txt))
    tel = [t.strip() for h in htmls for t in R_TEL.findall(h)]
    mail = [m.strip().lower() for h in htmls for m in R_MAIL.findall(h)]
    if tel:
        f["tel"] = Counter(tel).most_common(1)[0][0]
    if mail:
        f["mail"] = Counter(mail).most_common(1)[0][0]
    ceny = [float(c.replace(".", "").replace(",", ".")) if "," in c else float(c) for c in R_CENA.findall(txt)]
    ceny = [c for c in ceny if 100 <= c <= 2500]
    if ceny:
        f["cena"] = int(min(ceny))
    return f


def zpracuj(url):
    s = requests.Session()
    s.headers.update(UA)
    konec, html = stahni(url, s)
    if not html:
        return url, None
    htmls = [html]
    for u in odkazy(konec, html):
        _, h = stahni(u, s)
        if h:
            htmls.append(h)
        time.sleep(0.2)
    txt = " ".join(text(h) for h in htmls)
    f = fakta(txt, htmls)
    f["url"] = konec
    return url, f


def main():
    kity = json.load(open(os.path.join(OUT, "kitas-osm.json")))
    def web(t):
        u = (t.get("website") or t.get("contact:website") or t.get("url") or "").split(";")[0].strip()
        if u and not u.startswith("http"):
            u = "https://" + u
        return u
    podle = defaultdict(list)
    for k in kity:
        u = web(k["t"])
        if u:
            podle[u.rstrip("/")].append(k)
    # web, který sdílí víc Kit, je portál nosiče – fakta by nepatřila jedné Kitě
    vlastni = {u: ks[0] for u, ks in podle.items() if len(ks) == 1}
    print("webů:", len(podle), "vlastních:", len(vlastni), flush=True)
    vystup = {}
    hotovo = 0
    with ThreadPoolExecutor(max_workers=32) as ex:
        fut = {ex.submit(zpracuj, u): u for u in vlastni}
        for f in as_completed(fut):
            u, data = f.result()
            hotovo += 1
            if data:
                k = vlastni[u]
                data["datum"] = date.today().isoformat()
                # cenu bereme jen u Kit, které si ji určují samy (ne u obecních tabulek)
                if (k["t"].get("operator:type") or "") in ("public", "government"):
                    data.pop("cena", None)
                vystup[k["id"]] = data
            if hotovo % 500 == 0:
                print(hotovo, "/", len(vlastni), flush=True)
                json.dump(vystup, open(os.path.join(OUT, "weby.json"), "w"), ensure_ascii=False)
    json.dump(vystup, open(os.path.join(OUT, "weby.json"), "w"), ensure_ascii=False)
    pocty = Counter()
    for d in vystup.values():
        for k in ("provoz", "vek", "kapacita", "tel", "mail", "cena"):
            if k in d:
                pocty[k] += 1
    print("hotovo:", len(vystup), dict(pocty), flush=True)


if __name__ == "__main__":
    main()

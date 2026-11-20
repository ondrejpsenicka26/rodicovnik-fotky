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
VERZE = 4  # 4: zřizovatel, adresa, telefon a e-mail i z textu; weby z úředních dat a e-mailů
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
    "bilingual": re.compile(r"bilingual|zweisprachig|english[- ]speaking|deutsch[- ]englisch", re.I),
    "kneipp": re.compile(r"kneipp", re.I),
    "bewegung": re.compile(r"bewegungskita|bewegungskindergarten|anerkannter bewegungs|bewegte kita", re.I),
    # „Inklusion“ zmiňuje skoro každý web – počítáme jen konkrétní integrativní místa a skupiny
    "integrativ": re.compile(r"integrativ(?:e|er|en)?\s+(?:kita|kindergarten|kindertages\w*|einrichtung|gruppe|krippe)|"
                             r"integrationsplätze|integrationsplaetze|integrationskind|integrationsgruppe|"
                             r"heilpädagogische\s+(?:gruppe|kita|tagesstätte)|inklusive\s+(?:kita|kindertages\w*|einrichtung)", re.I),
}
VLASTNOSTI = {
    "zahrada": re.compile(r"\bgarten\b|außengelände|aussengelaende|außenspielgelände|\bgartens\b", re.I),
    "vlastni-kuchyne": re.compile(r"eigene[nr]? küche|eigene[nr]? kueche|frisch gekocht|täglich frisch|selbst gekocht", re.I),
    "bio-strava": re.compile(r"\bbio[- ]?(?:essen|kost|qualität|lebensmittel|zutaten|zertifiziert)", re.I),
    "logoped": re.compile(r"logopäd|logopaed|sprachförder", re.I),
    "plavani": re.compile(r"schwimm", re.I),
    "telocvicna": re.compile(r"turnraum|turnhalle|bewegungsraum|mehrzweckraum", re.I),
    "anglictina": re.compile(r"englisch|english", re.I),
    "vylety": re.compile(r"ausflüge|ausfluege|exkursion", re.I),
    "adaptace-s-rodicem": re.compile(r"berliner (?:eingewöhnungs)?modell|münchener (?:eingewöhnungs)?modell|eingewöhnung", re.I),
}
R_CENA = re.compile(r"(\d{3,4}(?:[.,]\d{2})?)\s*(?:€|euro|eur)\.?\s*(?:(?:pro|im|/|je)\s*monat|monatlich|mtl)", re.I)
R_CENA_KLIC = re.compile(r"beitrag|gebühr|gebuehr|kosten|elternentgelt|betreuungsentgelt|preis", re.I)
R_CENA_NE = re.compile(r"essen|verpflegung|mittag|anmelde|kaution|aufnahme|gehalt|vergütung|verguetung|stelle|erzieher", re.I)
R_TEL = re.compile(r'href="tel:([+\d][\d\s/()+-]{6,})"', re.I)
R_TEL_TEXT = re.compile(r"(?:tel(?:efon)?|fon|rufnummer)\.?\s*(?:nr\.?)?\s*[:.]?\s*((?:\+49|0049|\(?0)[\d\s/()\-–]{6,18}\d)", re.I)
R_MAIL_TEXT = re.compile(r"\b([a-z0-9][a-z0-9._%+-]{1,40}@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,10})\b", re.I)
# zřizovatel: „Träger der Einrichtung ist die Stadt X“, „Träger: AWO …“, „in Trägerschaft der …“
R_TRAEGER = [
    re.compile(r"Träger(?:in)?\s+(?:der|unserer|des|dieser|unseres|von)\s+(?:Einrichtung|Kita|Kindertagesstätte|"
               r"Kindertageseinrichtung|Kindergartens?|Kinderkrippe|Krippe|Kinderhaus(?:es)?|Hauses)[^.:]{0,40}?\s*"
               r"(?:ist|:)\s*(?:seit\s+\d{4}\s+)?(?:die|der|das)?\s*([A-ZÄÖÜ][^|•]{3,110})"),
    re.compile(r"(?:Unser|Der)\s+Träger\s+ist\s+(?:die|der|das)?\s*([A-ZÄÖÜ][^|•]{3,110})"),
    re.compile(r"\bTräger(?:in)?\s*:\s*(?:die|der|das)?\s*([A-ZÄÖÜ][^|•]{3,110})"),
    re.compile(r"in\s+(?:der\s+)?Trägerschaft\s+(?:der|des|von)\s+([A-ZÄÖÜ][^|•]{3,110})"),
    re.compile(r"(?:ist|sind)\s+eine\s+Einrichtung\s+(?:der|des)\s+([A-ZÄÖÜ][^|•]{3,110})"),
]
R_TRAEGER_KONEC = re.compile(r"\s(?:Tel\b|Telefon|Fax|E-?Mail|Mail\b|Anschrift|Adresse|Leitung|Leiterin|Kontakt|Öffnungszeit|"
                             r"Impressum|Datenschutz|Ansprechpartner|Vertreten|vertreten|Vorstand|Geschäftsführ|"
                             r"\d{5}\b|www\.|https?:|und\s+(?:wird|ist|hat|bietet|liegt)|\S*(?i:straße|strasse|str\.|weg|platz|allee|gasse|ring|damm)\s*\d)|"
                             r"(?<!\bSt)(?<!\bst)(?<!e\.V)(?<!e\. V)(?<!\be)(?<!\bDr)(?<!\b[Ee]v)(?<!\b[Kk]ath)(?<!\b[Ee]vang)(?<!\b[Ll]uth)(?<!\bgem)(?<!\bHl)\.(?:\s|$)|[;!?](?:\s|$)|\s[–—-]\s|,\s+(?:die|der|das|welche|und\s+wird|vertreten)\b|\(")
R_TRAEGER_ORG = re.compile(r"stadt|gemeinde|kirch|pfarr|bistum|diözese|dekanat|e\.\s?v|gmbh|\bgug\b|\bug\b|verein|verband|"
                           r"\bawo\b|arbeiterwohlfahrt|caritas|diakon|\bdrk\b|\bbrk\b|rotes kreuz|johanniter|malteser|"
                           r"lebenshilfe|stiftung|landkreis|\bmarkt\b|zweckverband|\bwerk\b|initiative|volkssolidarit|"
                           r"parit|\bamt\b|\bkreis\b|genossenschaft|\beg\b|universität|studierendenwerk|studentenwerk|"
                           r"fröbel|kinderschutzbund|\basb\b|samariter|\bkjf\b|kolping|orden|kloster|schwestern", re.I)
# adresa: schema.org nebo „Musterstraße 12, 12345 Ort“
R_ADR_LD = re.compile(r'"streetAddress"\s*:\s*"([^"]{4,80})"[^{}]{0,400}?"postalCode"\s*:\s*"(\d{5})"', re.S)
R_ADR_LD2 = re.compile(r'"postalCode"\s*:\s*"(\d{5})"[^{}]{0,400}?"streetAddress"\s*:\s*"([^"]{4,80})"', re.S)
R_ADR_TEXT = re.compile(r"((?:[A-ZÄÖÜ][\wäöüß.]*[ -]){0,4}(?:[A-ZÄÖÜa-zäöü][\wäöüß.-]*?)?(?i:straße|strasse|str\.|weg|platz|allee|gasse|"
                        r"ring|damm|ufer|chaussee|steig|stieg|pfad|hof|berg|markt|graben|wall|garten|feld|wiese|"
                        r"anger|grund|tal|höhe|bogen|zeile|kamp|brink|busch|bach|dorf|park|siedlung|promenade)"
                        r"\s+\d{1,4}(?:\s?[a-zA-Z]\b)?(?:\s?[-/]\s?\d{1,4})?)\s*[,|·•]?\s*(?:D[- ])?(\d{5})\s+"
                        r"([A-ZÄÖÜ][a-zäöüß]+(?:[ -](?:[A-ZÄÖÜ][a-zäöüß]+|am|an|der|im|in|ob|vor|bei|a\.|d\.)){0,4})")
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
    # web nosiče s více Kitami (…/kitas/nase-kita/): podstránky jen pod stejnou cestou,
    # jinak bychom četli stránky jiných Kit téhož nosiče
    cesta = urlparse(base).path
    if re.search(r"\.(html?|php|aspx?)$", cesta, re.I):
        cesta = cesta.rsplit("/", 1)[0] + "/"
    predpona = cesta if cesta.count("/") >= 2 and len(cesta.strip("/")) > 0 else "/"
    for h in re.findall(r'href="([^"#]+)"', html, re.I):
        try:
            u = urljoin(base, h)
            p = urlparse(u)
        except ValueError:
            continue
        if p.netloc != host or not p.scheme.startswith("http"):
            continue
        if not p.path.startswith(predpona):
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
    titul = " ".join(re.findall(r"<(?:title|h1)[^>]*>(.*?)</(?:title|h1)>", htmls[0], re.I | re.S))
    f["kat"] = sorted(k for k, rx in KAT.items() if len(rx.findall(txt)) >= 1)
    f["katSila"] = {k: [len(rx.findall(txt)), bool(rx.search(titul))] for k, rx in KAT.items() if rx.search(txt)}
    f["vlastnosti"] = sorted(k for k, rx in VLASTNOSTI.items() if rx.search(txt))
    tel = [t.strip() for h in htmls for t in R_TEL.findall(h)]
    mail = [m.strip().lower() for h in htmls for m in R_MAIL.findall(h)]
    if not tel:
        tel = [re.sub(r"\s+", " ", t).strip() for t in R_TEL_TEXT.findall(txt)]
    if not mail:
        mail = [m.lower() for m in R_MAIL_TEXT.findall(txt)
                if not re.search(r"\.(png|jpe?g|gif|webp|svg)$|sentry|wixpress|example|datenschutz|webmaster|noreply", m, re.I)]
    if tel:
        f["tel"] = Counter(tel).most_common(1)[0][0]
    if mail:
        f["mail"] = Counter(mail).most_common(1)[0][0]
    tr = traeger(txt)
    if tr:
        f["traeger"] = tr
    adr = adresy(txt, htmls)
    if adr:
        f["adr"] = adr
    ceny = []
    for m in R_CENA.finditer(txt):
        okoli = txt[max(0, m.start() - 140): m.start()]
        if not R_CENA_KLIC.search(okoli) or R_CENA_NE.search(okoli[-70:]):
            continue
        c = m.group(1)
        c = float(c.replace(".", "").replace(",", ".")) if "," in c else float(c)
        if 100 <= c <= 2500:
            ceny.append(c)
    if ceny:
        ceny.sort()
        f["cena"] = int(ceny[len(ceny) // 2])
        f["ceny"] = [int(c) for c in ceny[:6]]
    return f


def traeger(txt):
    """Název zřizovatele, jak ho uvádí web Kity (nebo None)."""
    kand = []
    for rx in R_TRAEGER:
        for m in rx.finditer(txt):
            t = m.group(1)
            k = R_TRAEGER_KONEC.search(t)
            if k:
                t = t[:k.start()]
            # za právní formou už název končí („… gGmbH Carl-Schwemmer-Straße 9“)
            pf = re.search(r"\b(?:g?GmbH|gUG|e\.\s?V\.?|eG|AöR|KdöR|gAG)(?=\s+[A-ZÄÖÜ0-9])", t)
            if pf:
                t = t[:pf.end()]
            t = re.sub(r"\s+", " ", t).strip(" ,:-–")
            if re.search(r"e\.\s?V$", t):
                t += "."
            if 4 <= len(t) <= 90 and R_TRAEGER_ORG.search(t) and len(t.split()) <= 12:
                kand.append(t)
        if kand:
            break
    return Counter(kand).most_common(1)[0][0] if kand else None


def adresy(txt, htmls):
    """Adresy uvedené na webu: [[ulice a číslo, PSČ, obec, kolikrát]], nejčastější první (nejvýš 3)."""
    c, obce = Counter(), {}
    for h in htmls:
        for u, p in R_ADR_LD.findall(h):
            c[(unescape(u).strip(), p)] += 3
        for p, u in R_ADR_LD2.findall(h):
            c[(unescape(u).strip(), p)] += 3
    for m in R_ADR_TEXT.finditer(txt):
        u = re.sub(r"\s+", " ", m.group(1)).strip()
        # před ulicí občas zůstane slovo z předchozí věty („Adresse Musterstraße 1“)
        u = re.sub(r"^(?:Adresse|Anschrift|Kontakt|Kita|Kindergarten|Kindertagesstätte|Impressum|Besucheradresse)\s+", "", u)
        c[(u, m.group(2))] += 1
        obce.setdefault((u, m.group(2)), m.group(3).strip())
    return [[u, p, obce.get((u, p), ""), n] for (u, p), n in c.most_common(3)]


def patri_kite(k, data):
    """Odhadnutý web (podle domény e-mailu) bereme, jen když uvádí adresu nebo jméno Kity."""
    t = k["t"]
    plz, ulice = t.get("addr:postcode"), (t.get("addr:street") or "").lower()
    for a in data.get("adr") or []:
        if plz and a[1] == plz:
            return True
        if ulice and ulice[:8] in a[0].lower():
            return True
    slova = [w for w in re.findall(r"[a-zäöüß]{5,}", (t.get("name") or "").lower())
             if w not in ("kindergarten", "kindertagesstätte", "kinderhaus", "kinderkrippe", "städtische",
                          "städtischer", "evangelische", "evangelischer", "katholische", "katholischer",
                          "kindertageseinrichtung", "tageseinrichtung", "kinder")]
    titul = (data.get("titul") or "").lower()
    return bool(slova) and all(w in titul for w in slova[:2])


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
    f["titul"] = re.sub(r"\s+", " ", unescape(" ".join(
        re.findall(r"<(?:title|h1)[^>]*>(.*?)</(?:title|h1)>", htmls[0], re.I | re.S))))[:200]
    f["titul"] = re.sub(r"<[^>]+>", " ", f["titul"]).strip()
    f["url"] = konec
    return url, f


def main():
    kity = json.load(open(os.path.join(OUT, "kitas-osm.json")))
    def web(t):
        u = (t.get("website") or t.get("contact:website") or t.get("url") or "").split(";")[0].strip()
        if u and not u.startswith("http"):
            u = "https://" + u
        return u
    # weby, které OSM nemá: z úředních seznamů („amt“) a odhad podle domény e-mailu („mail“)
    try:
        extra = json.load(open(os.path.join(OUT, "weby-extra.json")))
    except Exception:  # noqa: BLE001
        extra = {}
    puvod = {}
    podle = defaultdict(list)
    for k in kity:
        u = web(k["t"])
        if not u and k["id"] in extra:
            u = extra[k["id"]]["url"]
            puvod[k["id"]] = extra[k["id"]].get("zdroj", "amt")
        if u:
            podle[u.rstrip("/")].append(k)
    # web, který sdílí víc Kit, je portál nosiče – fakta by nepatřila jedné Kitě
    PORTALY = re.compile(r"little-bird\.de|(^|\.)kita\.de|kitanetz|kita-?portal|meinkitaplatz|kita-?navigator|"
                         r"kitafinder|kinderbetreuung-|familienportal|kita-info|kitaplatz-", re.I)
    vlastni = {u: ks[0] for u, ks in podle.items() if len(ks) == 1 and not PORTALY.search(urlparse(u).netloc)}
    print("webů:", len(podle), "vlastních:", len(vlastni), flush=True)
    # pokračování: co už máme z minulého běhu, znovu nestahujeme
    cesta = os.path.join(OUT, "weby.json")
    try:
        vystup = json.load(open(cesta))
    except Exception:  # noqa: BLE001
        vystup = {}
    vystup = {k: v for k, v in vystup.items() if v.get("v") == VERZE}
    zbyva = {u: k for u, k in vlastni.items() if k["id"] not in vystup}
    print("už hotovo:", len(vystup), "zbývá:", len(zbyva), flush=True)
    hotovo = 0

    def bezpecne(u):
        try:
            return zpracuj(u)
        except Exception as e:  # noqa: BLE001 – jeden rozbitý web nesmí shodit celý běh
            print("chyba", u, str(e)[:120], flush=True)
            return u, None

    with ThreadPoolExecutor(max_workers=32) as ex:
        fut = {ex.submit(bezpecne, u): u for u in zbyva}
        for f in as_completed(fut):
            u, data = f.result()
            hotovo += 1
            if data:
                k = vlastni[u]
                if k["id"] in puvod:
                    data["puvodWebu"] = puvod[k["id"]]
                    if puvod[k["id"]] == "mail" and not patri_kite(k, data):
                        data = {"url": data.get("url", u), "neovereno": True}
                data["datum"] = date.today().isoformat()
                data["v"] = VERZE
                # cenu bereme jen u Kit, které si ji určují samy (ne u obecních tabulek)
                if (k["t"].get("operator:type") or "") in ("public", "government"):
                    data.pop("cena", None)
                vystup[k["id"]] = data
            if hotovo % 250 == 0:
                print(hotovo, "/", len(zbyva), flush=True)
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

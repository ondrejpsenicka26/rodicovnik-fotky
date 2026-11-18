"""Které IČO je na webu: pro sdílené weby zjistí, jestli web uvádí IČO dané školky.

Vstup:  weby-csi/ico-vstup.json  {slug: {web, ico}}
Výstup: weby-csi/ico.json        {slug: {ico_na_webu: bool, chyba: str}}
Stahuje úvodní stránku a nejvýš 5 kontaktních stránek; respektuje robots.txt.
"""
import html, json, re, socket, time, unicodedata, urllib.parse, urllib.request, urllib.robotparser
from concurrent.futures import ThreadPoolExecutor

UA = "RodicovnikBot/1.0 (+https://github.com/ondrejpsenicka26/rodicovnik-fotky; prehled skolek v CR)"
_gai = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [x for x in _gai(*a, **k) if x[0] == socket.AF_INET] or _gai(*a, **k)
KONTAKT = re.compile(r"kontakt|o-nas|o-skol|zakladni-udaje|zakladni-informace|informace|uvod|o-ms|povinne|gdpr|dokument", re.I)
_robots, _cache = {}, {}


def bez(s):
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")


def smim(u):
    p = urllib.parse.urlparse(u)
    k = f"{p.scheme}://{p.netloc}"
    if k not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(urllib.request.urlopen(urllib.request.Request(k + "/robots.txt", headers={"User-Agent": UA}), timeout=15)
                     .read(300_000).decode("utf-8", "replace").splitlines())
        except Exception:  # noqa: BLE001
            rp = None
        _robots[k] = rp
    return True if _robots[k] is None else _robots[k].can_fetch(UA, u)


def nacti(u):
    if u in _cache:
        return _cache[u]
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=30) as r:
        v = (r.geturl(), r.read(3_000_000).decode("utf-8", "replace"))
    _cache[u] = v
    return v


def cisla_z(t):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.I | re.S)
    return re.sub(r"\s", "", bez(html.unescape(re.sub(r"<[^>]+>", " ", t))))


def over(item):
    slug, z = item
    try:
        if not smim(z["web"]):
            return slug, {"ico_na_webu": None, "chyba": "robots"}
        final, t = nacti(z["web"])
        texty = [cisla_z(t)]
        host = urllib.parse.urlparse(final).netloc
        odk = []
        for m in re.finditer(r'<a[^>]+href=["\']([^"\'#]+)["\']', t, re.I):
            u = urllib.parse.urljoin(final, html.unescape(m.group(1)))
            if urllib.parse.urlparse(u).netloc == host and KONTAKT.search(bez(urllib.parse.unquote(u))) and u not in odk:
                odk.append(u)
        for u in sorted(odk, key=lambda u: 0 if "kontakt" in u.lower() else 1)[:5]:
            if smim(u):
                time.sleep(1)
                try:
                    texty.append(cisla_z(nacti(u)[1]))
                except Exception:  # noqa: BLE001
                    pass
        ico = z["ico"].lstrip("0")
        return slug, {"ico_na_webu": bool(ico) and any(z["ico"] in x for x in texty), "chyba": ""}
    except Exception as e:  # noqa: BLE001
        return slug, {"ico_na_webu": None, "chyba": str(e)[:80]}


vstup = json.load(open("weby-csi/ico-vstup.json"))
with ThreadPoolExecutor(4) as ex:
    vysl = dict(ex.map(over, sorted(vstup.items())))
json.dump(vysl, open("weby-csi/ico.json", "w"), ensure_ascii=False, indent=0, sort_keys=True)
print(sum(1 for v in vysl.values() if v["ico_na_webu"]), "s IČO z", len(vysl))

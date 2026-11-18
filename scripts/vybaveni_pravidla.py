"""Přesná pravidla nad úryvky z crawleru (vybaveni/cast-*.json)."""
import re, unicodedata

def bez(s):
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower()) if unicodedata.category(c) != "Mn")

# (klíč úryvků, vlastnost, regex bez diakritiky)
PRAVIDLA = [
    ("zahrad", "zahrada", r"(skolni|nasi|nase|vlastni|rozlehl\w*|velk\w*|prostorn\w*|krasn\w*|oplocen\w*|udrzovan\w*|prirodni|travnat\w*|zrekonstruovan\w*|nove upraven\w*|vybaven\w*) zahrad|zahrad[auye]? (skolky|materske skoly|ms|je vybaven|s pisk|s herni|s hristem|s prvky|s preliz|nabizi|obklopuje|kolem budovy)|na (nasi|skolni|velke|prostorne|vlastni|rozlehle) zahrade|na zahrade (skolky|materske skoly|ms)|pobyt na zahrade|pobytu na zahrade|vyuziva\w* zahrad|soucasti (skolky|ms|budovy) je (i )?zahrad"),
    ("kuchyn", "vlastni-kuchyne", r"vlastni (skolni )?(kuchyn|jidel|vyvarovn)|varime (si )?(sami|vlastni|ve vlastni|z cerstv|pro deti)|(jidl\w*|strav\w*|obed\w*) (se )?(pripravuj\w*|vari\w*|vari se) (prim[oe] )?(v|ve) (nasi|vlastni|nasi vlastni|skolni) (kuchyn|jidel)|kuchyn\w* (primo )?v budove (skolky|ms)"),
    ("bio", "bio-strava", r"\bbio ?(potravin|strav|kvalit|produkt|surovin|mlek|ovoc|zelenin|biedermann)|biopotravin|biostrav|z biofarm|ekologicke(ho)? zemedel"),
    ("logoped", "logoped", r"logoped"),
    ("plav", "plavani", r"predplaveck|plaveck\w* (vycvik|kurz|vychov|vyuk)|kurz\w* plavani|vyuk\w* plavani|plavani (v bazenu|deti|pro deti|predskolak)|chodime plavat|jezdime plavat|jezdime na plavani|chodime na plavani"),
    ("vylet", "vylety", r"skol[ayu] v prirode|vylet[yu]? (do|na|za|s )|vyletujeme|jezdime na vylet|poradame vylet"),
    ("bezbar", "bezbarierovy", r"bezbarierov\w* (pristup|vstup|budov|zazemi|prostor|trid)|budova je bezbarierov|bezbarierovy|bezbarierova|plosin\w* pro (vozick|imobil)"),
    ("adaptac", "adaptace-s-rodicem", r"adaptac[^.]{0,120}(?<!spolupraci )(?<!komunikaci )(s rodicem|spolecne s rodic|v doprovodu rodic|pritomnost\w* rodic|rodic\w* (muze|mohou|muzou|smi) (zustat|byt (pritom|s ditetem|ve tride|pritomn))|rodic\w* (pritomen|pritomni|zustava|zustavaji))|(pobyt\w* (ditete )?s rodicem|rodic\w* (muze|mohou|muzou|smi) (zustat|byt (pritom|s ditetem|ve tride|pritomn))|pritomnost\w* rodic)[^.]{0,120}adaptac"),
    ("celorocni", "celorocni-provoz", r"celorocn\w* provoz|provoz (je )?(i|take|rovnez) (o|pres) (letni\w* |vsech )?prazdnin|otevren\w* (i )?(o|pres) (letni\w* |vsech )?prazdnin|bez letniho preruseni|neprerusuj\w* provoz|v provozu cel[yo] rok|otevreno cely rok|celorocne otevren"),
    ("jurta", "jurta", r"\bjurt[aeuy]?\b|\bjurte\b|maringotk"),
    ("solna", "solna-jeskyne", r"soln[aeyou] (jeskyn|mistnost|komor)"),
    ("sauna", "sauna", r"saunov|\bsaun[aeu]\b|do sauny"),
    ("keramika", "keramika", r"keramik|keramick\w* (dilna|pec|krouz|tvoren)"),
    ("anglictina", "anglictina", r"anglictin|anglick\w* (jazyk|krouz|hrav|konverza)|krouz\w* anglic|english (club|time|krouzek|for kids|lessons|with|kids)|hrava anglictina"),
    ("krouzky", "krouzky", r"krouzk|\bkrouzek"),
    ("telocvicna", "telocvicna", r"telocvicn"),
    ("lyze", "lyzovani", r"lyzarsk\w* (vycvik|kurz|skol|skolick|skolk)|lyzovani|na lyzich|na lyze\b"),
    ("brusle", "brusleni", r"bruslen|bruslarsk|na bruslich|na brusle\b"),
]
PRAVIDLA = [(k, v, re.compile(r)) for k, v, r in PRAVIDLA]
NEGACE = re.compile(r"(\btemer\b|\bcastecn\w*|\bne\b|\bneni\b|\bnejsou\b|\bnemame\b|\bnema\b|\bnemaji\b|\bnenabiz\w*|\bnelze\b|\bbohuzel\b|\bchybi\b|\bzakaz\w*|\bbez\b|\bzatim\b|\bnebude\b|\bneposkyt\w*|\bnezajist\w*|\bnebyl\w*)[^.!?]{0,45}$")
MS = re.compile(r"\bms\b|matersk|skolk|predskol|dets\w* skupin|\bds\b")
# úryvky, které nejsou o školce samotné (nabídky okolí, zprávy obce, pozvánky jiných)
CIZI = re.compile(r"obecni urad|zastupitel|usneseni|svoz odpadu|pronajem|inzer|prodam|koupim")


HISTORIE = re.compile(r"histori|kronik")
ZS_URL = re.compile(r"/zakladni-skol|/zs(/|-|$)|/zs\b|/druhy-stupen|/prvni-stupen|/zaci|/pro-zaky")
NA_AKCI = re.compile(r"bezbarierov\w* vstup na akci|vstup na akci")
MS_URL = re.compile(r"/(ms|materska[\w-]*|skolka[\w-]*|mat-skola|ms-[\w-]+)(/|$)")
# znaky, že jde o základní školu (třídy, žáci, družina)
ZS_ZNAKY = re.compile(r"\d\.\s?(a \d\.\s?)?(trid|rocnik)|\bzak|\bzaci|\bzaku|druzin|\bsd\b|stupn")
# název sdružené školy („Základní škola a Mateřská škola X“, „ZŠ a MŠ“) se jako zmínka o MŠ nepočítá
NAZEV_ZS_MS = re.compile(r"zakladni skol\w*[^.]{0,40}?matersk\w* skol\w*|\bzs\s*(a|,|&)\s*ms\b")
STARY_ROK = re.compile(r"\b19[0-9]{2}\b")


def vyhodnot(rec, zs):
    """rec = výstup crawleru pro školku; zs = ZŠ i MŠ (sdružená škola): vyžadovat zmínku o MŠ v úryvku."""
    nalez = {}
    for k, vlast, rx in PRAVIDLA:
        if vlast in nalez:
            continue
        for url, uryvek in rec.get("kontext", {}).get(k, []):
            b = bez(uryvek)
            cesta = bez(url.split("/", 3)[-1]) if url.count("/") >= 3 else ""
            if CIZI.search(b) or HISTORIE.search(cesta) or NA_AKCI.search(b):
                continue
            if zs and ZS_URL.search("/" + cesta):
                continue
            if vlast in ("sauna", "celorocni-provoz") and STARY_ROK.search(b):
                continue
            for m in rx.finditer(b):
                pred = b[max(0, m.start() - 60):m.start()]
                if NEGACE.search(pred):
                    continue
                # sdružená ZŠ a MŠ: buď stránka školky, nebo zmínka o školce těsně u nálezu
                # a ne v menu (menu ZŠ a MŠ mívá „kroužky“ i „MŠ“ vedle sebe)
                if zs:
                    blizko = b[max(0, m.start() - 60):m.end() + 60]
                    if ZS_ZNAKY.search(blizko):
                        continue
                    if not MS_URL.search("/" + cesta):
                        okoli = b[max(0, m.start() - 100):m.end() + 100]
                        if not MS.search(NAZEV_ZS_MS.sub(" ", blizko)) or okoli.count(" . ") >= 5:
                            continue
                nalez[vlast] = (url, uryvek)
                break
            if vlast in nalez:
                break
    return nalez

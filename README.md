# Fotky školek pro Rodičovník

Fotky budov, tříd a zahrad mateřských škol a dětských skupin v Česku pro web Rodičovník.
Pocházejí z webů jednotlivých školek a používáme je se svolením. **Nepoužívejte je jinde.**
Pokud vedete školku a chcete fotky změnit nebo odstranit, napište nám přes web Rodičovník.

- Žádné fotky lidí ani dětí. Každou fotku kontroluje detektor osob (YOLO) a vyřadí ji,
  pokud na ní je kdokoli. Při ruční kontrole ji pak vyřadíme taky.
- Max. 4 fotky na školku, WebP do 1200 px bez EXIF a náhled 560 px.
- Zveřejněné přes GitHub Pages: `https://ondrejpsenicka26.github.io/rodicovnik-fotky/fotky/<slug>/<n>.webp`.

## Jak to běží

1. `cile.json` – weby školek (`{web: {slug, jen_ms}}`). Generuje se z dat webu Rodičovník.
2. Workflow **Fotky školek** (`.github/workflows/fotky.yml`) rozdělí weby na části. Každá část
   projde weby, roztřídí fotky (CLIP: budova / interiér / zahrada / les / umývárna, YOLO: lidé)
   a uloží výsledky do `fotky/`, `manifest/<část>.json` a `hotovo/<část>.json`.
3. Přerušený běh stačí spustit znovu (Actions → Fotky školek → Run workflow) a hotové weby se přeskočí.

Stahování je ohleduplné: řídí se `robots.txt` a na jeden web posílá nejvýš pár požadavků za sekundu.

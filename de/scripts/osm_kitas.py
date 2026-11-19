"""Kitas v Německu z OpenStreetMap + přiřazení obci (VG250-EW, BKG).

Výstupy (de/data):
  kitas-osm.json   – seznam zařízení: OSM id, název, souřadnice, vybrané tagy, AGS obce
  gemeinden.json   – obce: AGS, název, druh, okres, země, počet obyvatel, střed
Licence: OSM © přispěvatelé OpenStreetMap (ODbL); VG250-EW © BKG dl-de/by-2-0.
"""
import io, json, os, sys, time, zipfile
import requests
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

OUT = os.path.join(os.path.dirname(__file__), "..", "data")
UA = {"User-Agent": "kita-finder-data/1.0 (github.com/ondrejpsenicka26/rodicovnik-fotky)"}
LAENDER = ["SH", "HH", "NI", "HB", "NW", "HE", "RP", "BW", "BY", "SL", "BE", "BB", "MV", "SN", "ST", "TH"]
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
TAGY = ["name", "official_name", "alt_name", "operator", "operator:type", "denomination", "religion",
        "website", "contact:website", "url", "phone", "contact:phone", "email", "contact:email",
        "opening_hours", "capacity", "min_age", "max_age", "wheelchair", "addr:street", "addr:housenumber",
        "addr:postcode", "addr:city", "addr:suburb", "kindergarten:type", "nursery", "isced:level",
        "after_school", "pedagogy", "description", "start_date", "wikidata", "image", "wikimedia_commons"]


def overpass(kod):
    q = f"""[out:json][timeout:900];
area["ISO3166-2"="DE-{kod}"]["admin_level"="4"]->.a;
(nwr["amenity"="kindergarten"](area.a); nwr["amenity"="childcare"](area.a););
out center tags;"""
    for pokus in range(6):
        url = OVERPASS[pokus % len(OVERPASS)]
        try:
            r = requests.post(url, data={"data": q}, headers=UA, timeout=1000)
            if r.status_code == 200:
                return r.json()["elements"]
            print(kod, url, r.status_code, flush=True)
        except Exception as e:
            print(kod, url, e, flush=True)
        time.sleep(30 * (pokus + 1))
    raise SystemExit(f"Overpass selhal pro {kod}")


def stahni(u, cil):
    """Stažení velkého souboru s navazováním (Range) po přerušení."""
    for pokus in range(8):
        hotovo = os.path.getsize(cil) if os.path.exists(cil) else 0
        h = dict(UA, **({"Range": f"bytes={hotovo}-"} if hotovo else {}))
        try:
            with requests.get(u, headers=h, stream=True, timeout=120) as r:
                if r.status_code == 416:
                    return True
                if r.status_code not in (200, 206):
                    print("VG250", u, r.status_code, flush=True)
                    return False
                if r.status_code == 200 and hotovo:
                    open(cil, "wb").close()
                celkem = int(r.headers.get("Content-Length", 0)) + (hotovo if r.status_code == 206 else 0)
                with open(cil, "ab") as f:
                    for kus in r.iter_content(1 << 20):
                        f.write(kus)
                if not celkem or os.path.getsize(cil) >= celkem:
                    return True
        except Exception as e:
            print("VG250 pokus", pokus, e, flush=True)
        time.sleep(15)
    return False


def vg250():
    u = "https://daten.gdz.bkg.bund.de/produkte/vg/vg250-ew_ebenen_1231/aktuell/vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
    cil = "/tmp/vg250.zip"
    if not stahni(u, cil):
        raise SystemExit("VG250 se nepodařilo stáhnout")
    z = zipfile.ZipFile(cil)
    gpkg = [n for n in z.namelist() if n.endswith(".gpkg")][0]
    z.extract(gpkg, "/tmp/vg")
    return os.path.join("/tmp/vg", gpkg)


def kompakt(e):
    lat = e.get("lat") or (e.get("center") or {}).get("lat")
    lon = e.get("lon") or (e.get("center") or {}).get("lon")
    t = e.get("tags", {})
    return {"id": f"{e['type'][0]}{e['id']}", "lat": round(lat, 6) if lat else None,
            "lon": round(lon, 6) if lon else None, "amenity": t.get("amenity"),
            "t": {x: t[x] for x in TAGY if x in t}}


def main():
    os.makedirs(os.path.join(OUT, "osm"), exist_ok=True)
    # 1) syrová data po zemích (uložená hned, při dalším běhu se už nestahují)
    prvky = {}
    for kod in LAENDER:
        soubor = os.path.join(OUT, "osm", f"{kod}.json")
        if os.path.exists(soubor) and os.environ.get("OSM_ZNOVU") != "1":
            el = json.load(open(soubor))
        else:
            el = [kompakt(e) for e in overpass(kod)]
            el = [e for e in el if e["lat"] is not None]
            json.dump(el, open(soubor, "w"), ensure_ascii=False, separators=(",", ":"))
            time.sleep(20)
        for e in el:
            prvky[e["id"]] = e
        print(kod, len(el), "celkem", len(prvky), flush=True)

    path = vg250()
    gem = gpd.read_file(path, layer="vg250_gem")
    krs = gpd.read_file(path, layer="vg250_krs")
    lan = gpd.read_file(path, layer="vg250_lan")
    print("vrstvy", len(gem), len(krs), len(lan), list(gem.columns), flush=True)
    if "GF" in gem.columns:
        gem = gem[gem["GF"] == 4]
    gem = gem.to_crs(25832)

    zaznamy = list(prvky.values())
    body = gpd.GeoDataFrame(zaznamy, geometry=[Point(z["lon"], z["lat"]) for z in zaznamy], crs=4326).to_crs(25832)
    spoj = gpd.sjoin(body, gem[["AGS", "geometry"]], how="left", predicate="within")
    spoj = spoj[~spoj.index.duplicated(keep="first")]
    for i, z in enumerate(zaznamy):
        ags = spoj.iloc[i]["AGS"]
        z["ags"] = ags if isinstance(ags, str) else None
    zaznamy = [z for z in zaznamy if z["ags"]]

    kn = dict(zip(krs["AGS"], krs["GEN"] + "|" + krs["BEZ"]))
    ln = dict(zip(lan["AGS"], lan["GEN"]))
    stredy = gem.copy()
    stredy["geometry"] = stredy.representative_point()
    stredy = stredy.to_crs(4326)
    obce = {}
    for _, r in stredy.iterrows():
        ags = r["AGS"]
        if ags in obce:
            continue
        kr = (kn.get(ags[:5]) or "|").split("|")
        obce[ags] = {"ags": ags, "name": r["GEN"], "bez": r.get("BEZ"), "kreis": kr[0], "kreisBez": kr[1],
                     "land": ln.get(ags[:2]), "ewz": int(r["EWZ"]) if "EWZ" in r and pd.notna(r["EWZ"]) else None,
                     "lat": round(r.geometry.y, 5), "lon": round(r.geometry.x, 5)}
    json.dump(zaznamy, open(os.path.join(OUT, "kitas-osm.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    json.dump(obce, open(os.path.join(OUT, "gemeinden.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    print("hotovo", len(zaznamy), "zařízení,", len(obce), "obcí", flush=True)


if __name__ == "__main__":
    main()

"""Geografie pro Kita Finder: okresy (Kreise), země, sousedé, SVG mapa a čtvrti velkých měst.

Výstupy (de/data/geo):
  kreise.json     – Kreise: AGS, název, druh, země, obyvatelé, střed, sousední Kreise
  laender.json    – země: AGS, název, obyvatelé, střed
  mapa.json       – zjednodušené hranice zemí a Kreise jako SVG cesty (pro mapy na webu)
  stadtteile.json – velká města: městské obvody (Stadtbezirke) z OSM a přiřazení Kit k nim
Licence: VG250-EW © BKG dl-de/by-2-0; obvody měst © přispěvatelé OpenStreetMap (ODbL).
"""
import json, math, os, sys, time
import requests
import geopandas as gpd
from shapely.geometry import Point, Polygon, MultiPolygon, LineString
from shapely.ops import polygonize, unary_union, linemerge

sys.path.insert(0, os.path.dirname(__file__))
from osm_kitas import vg250, UA, OVERPASS  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "data")
GEO = os.path.join(OUT, "geo")

# Projekce do SVG: jednoduchá (lng·cos 51°, −lat), stejná jako u mapy ČR na Rodičovníku.
KX = math.cos(math.radians(51.2))
SIRKA = 800.0


def svg(geom, minx, miny, s):
    parts = []
    polys = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    for p in polys:
        if p.is_empty or p.area * s * s < 0.6:  # drobné ostrůvky vynechat
            continue
        for ring in [p.exterior]:
            pts = [((x * KX - minx) * s, (-y - miny) * s) for x, y in ring.coords]
            out, last = [], None
            for x, y in pts[:-1]:
                k = (round(x, 1), round(y, 1))
                if k != last:
                    out.append(k)
                    last = k
            if len(out) >= 3:
                parts.append("M" + "L".join(f"{x},{y}" for x, y in out) + "Z")
    return "".join(parts)


def overpass(q):
    for pokus in range(6):
        url = OVERPASS[pokus % len(OVERPASS)]
        try:
            r = requests.post(url, data={"data": q}, headers=UA, timeout=600)
            if r.status_code == 200:
                return r.json()["elements"]
            print(url, r.status_code, flush=True)
        except Exception as e:
            print(url, e, flush=True)
        time.sleep(20 * (pokus + 1))
    return None


def polygon_relace(el):
    cary = []
    for m in el.get("members", []):
        if m.get("type") == "way" and m.get("role") in ("outer", "") and m.get("geometry"):
            cary.append(LineString([(g["lon"], g["lat"]) for g in m["geometry"]]))
    if not cary:
        return None
    polys = list(polygonize(linemerge(unary_union(cary))))
    if not polys:
        return None
    g = unary_union(polys)
    return g if g.is_valid else g.buffer(0)


def main():
    os.makedirs(GEO, exist_ok=True)
    path = vg250()
    krs = gpd.read_file(path, layer="vg250_krs")
    lan = gpd.read_file(path, layer="vg250_lan")
    gem = gpd.read_file(path, layer="vg250_gem")
    for df in (krs, lan, gem):
        if "GF" in df.columns:
            df.drop(df[df["GF"] != 4].index, inplace=True)
    krs = krs.to_crs(25832)
    lan = lan.to_crs(25832)
    gem = gem.to_crs(25832)

    # --- sousedé Kreise (sdílená hranice delší než ~1 km) ---
    obrys = krs[["AGS", "geometry"]].copy()
    obrys["geometry"] = obrys.buffer(60)
    par = gpd.sjoin(obrys, obrys, predicate="intersects")
    sousede = {}
    geom = dict(zip(krs["AGS"], krs.geometry))
    for a, b in zip(par["AGS_left"], par["AGS_right"]):
        if a == b:
            continue
        styk = geom[a].buffer(60).intersection(geom[b].buffer(60))
        if styk.area > 60_000:  # ~ 1 km společné hranice
            sousede.setdefault(a, set()).add(b)

    def stred(g):
        p = gpd.GeoSeries([g.representative_point()], crs=25832).to_crs(4326).iloc[0]
        return [round(p.y, 4), round(p.x, 4)]

    kreise = []
    for _, r in krs.iterrows():
        kreise.append({"ags": r["AGS"], "name": r["GEN"], "bez": r["BEZ"], "land": r["AGS"][:2],
                       "ewz": int(r["EWZ"]) if "EWZ" in r and r["EWZ"] == r["EWZ"] else None,
                       "stred": stred(r.geometry), "sousede": sorted(sousede.get(r["AGS"], []))})
    laender = [{"ags": r["AGS"], "name": r["GEN"],
                "ewz": int(r["EWZ"]) if "EWZ" in r and r["EWZ"] == r["EWZ"] else None,
                "stred": stred(r.geometry)} for _, r in lan.iterrows()]
    json.dump(kreise, open(os.path.join(GEO, "kreise.json"), "w"), ensure_ascii=False)
    json.dump(laender, open(os.path.join(GEO, "laender.json"), "w"), ensure_ascii=False)
    print("kreise", len(kreise), "laender", len(laender), flush=True)

    # --- SVG mapa ---
    k4 = krs.copy()
    k4["geometry"] = k4.simplify(700, preserve_topology=True)
    k4 = k4.to_crs(4326)
    l4 = lan.dissolve(by="AGS").reset_index()
    l4["geometry"] = l4.simplify(900, preserve_topology=True)
    l4 = l4.to_crs(4326)
    bx0, by0, bx1, by1 = l4.total_bounds
    minx, miny = bx0 * KX, -by1
    s = SIRKA / ((bx1 - bx0) * KX)
    vyska = (by1 - by0) * s

    def polozka(ags, g):
        d = svg(g, minx, miny, s)
        x0, y0, x1, y1 = g.bounds
        c = g.representative_point()
        return {"ags": ags, "d": d, "cx": round((c.x * KX - minx) * s, 1), "cy": round((-c.y - miny) * s, 1),
                "box": [round((x0 * KX - minx) * s, 1), round((-y1 - miny) * s, 1),
                        round((x1 * KX - minx) * s, 1), round((-y0 - miny) * s, 1)]}

    mapa = {"sirka": SIRKA, "vyska": round(vyska, 1), "minx": minx, "miny": miny, "s": s, "kx": KX,
            "laender": [polozka(r["AGS"], r.geometry) for _, r in l4.iterrows()],
            "kreise": [dict(polozka(r["AGS"], r.geometry), land=r["AGS"][:2]) for _, r in k4.iterrows()]}
    json.dump(mapa, open(os.path.join(GEO, "mapa.json"), "w"), ensure_ascii=False, separators=(",", ":"))
    print("mapa", round(vyska), "px vysoka,", sum(len(x["d"]) for x in mapa["kreise"]) // 1000, "kB kreise",
          flush=True)

    # --- GeoJSON hranic Kreise po zemích (živá mapa země na webu) ---
    os.makedirs(os.path.join(GEO, "kreise-geo"), exist_ok=True)
    kg = krs.copy()
    kg["geometry"] = kg.simplify(150, preserve_topology=True)
    kg = kg.to_crs(4326)
    for land, df in kg.groupby(kg["AGS"].str[:2]):
        feats = []
        for _, r in df.iterrows():
            g = r.geometry
            gj = json.loads(gpd.GeoSeries([g], crs=4326).to_json())["features"][0]["geometry"]

            def zaokrouhli(c):
                return [round(c[0], 4), round(c[1], 4)] if isinstance(c[0], float) else [zaokrouhli(x) for x in c]

            gj["coordinates"] = zaokrouhli(gj["coordinates"])
            feats.append({"type": "Feature", "properties": {"ags": r["AGS"], "name": r["GEN"], "bez": r["BEZ"]},
                          "geometry": gj})
        x0, y0, x1, y1 = df.total_bounds
        json.dump({"bbox": [round(x0, 4), round(y0, 4), round(x1, 4), round(y1, 4)],
                   "oblasti": {"type": "FeatureCollection", "features": feats}},
                  open(os.path.join(GEO, "kreise-geo", f"{land}.json"), "w"), ensure_ascii=False,
                  separators=(",", ":"))
    print("kreise-geo hotovo", flush=True)

    # --- čtvrti velkých měst (OSM admin_level 9 a 10) ---
    kity_soubor = os.path.join(OUT, "kitas-osm.json")
    if not os.path.exists(kity_soubor):
        print("kitas-osm.json chybí – čtvrti přeskočeny")
        return
    kity = json.load(open(kity_soubor))
    pocty = {}
    for k in kity:
        pocty[k["ags"]] = pocty.get(k["ags"], 0) + 1
    gem4 = gem.to_crs(4326)
    mesta = gem4[gem4["AGS"].map(lambda a: pocty.get(a, 0) >= 120)]
    vysledek = {}
    for _, m in mesta.iterrows():
        ags, nazev = m["AGS"], m["GEN"]
        x0, y0, x1, y1 = m.geometry.bounds
        q = f"""[out:json][timeout:300];
rel["boundary"="administrative"]["admin_level"~"^(9|10)$"]({y0},{x0},{y1},{x1});
out geom;"""
        el = overpass(q)
        time.sleep(8)
        if not el:
            print(nazev, "bez dat", flush=True)
            continue
        oblasti = {"9": [], "10": []}
        mesto = m.geometry.buffer(0)
        for e in el:
            t = e.get("tags", {})
            lvl, jm = t.get("admin_level"), t.get("name")
            if lvl not in oblasti or not jm:
                continue
            g = polygon_relace(e)
            if g is None or g.is_empty:
                continue
            # jen obvody uvnitř města (sousední obce mají vlastní stránky)
            if g.intersection(mesto).area < 0.8 * g.area:
                continue
            oblasti[lvl].append((jm, g))
        moje = [k for k in kity if k["ags"] == ags]
        prirazeni = {}
        for k in moje:
            p = Point(k["lon"], k["lat"])
            hit = []
            for lvl in ("9", "10"):
                jm = next((n for n, g in oblasti[lvl] if g.contains(p)), None)
                hit.append(jm)
            prirazeni[k["id"]] = hit
        # hranice obvodů (pro živou mapu Berlína a Hamburku)
        feats = []
        for jm, g in oblasti["9"]:
            gs = gpd.GeoSeries([g], crs=4326).to_crs(25832).simplify(80).to_crs(4326)
            gj = json.loads(gs.to_json())["features"][0]["geometry"]
            feats.append({"type": "Feature", "properties": {"name": jm}, "geometry": gj})
        if feats:
            json.dump({"type": "FeatureCollection", "features": feats},
                      open(os.path.join(GEO, f"bezirke-{ags}.json"), "w"), ensure_ascii=False, separators=(",", ":"))
        vysledek[ags] = {"name": nazev, "bezirke": sorted({n for n, _ in oblasti["9"]}),
                         "stadtteile": sorted({n for n, _ in oblasti["10"]}), "kity": prirazeni}
        print(nazev, len(moje), "Kit,", len(oblasti["9"]), "obvodů,", len(oblasti["10"]), "čtvrtí", flush=True)
    json.dump(vysledek, open(os.path.join(GEO, "stadtteile.json"), "w"), ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()

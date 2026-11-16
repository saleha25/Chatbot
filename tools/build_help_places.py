"""Build data/help_places.json for the Practical Help map (run ONCE on your laptop).

It reads the free OpenStreetMap shapefiles for Pakistan from Geofabrik and keeps only the
places an IDP may need (hospitals, pharmacies, police, rescue, NADRA, NGOs, food, shelter,
women protection, DC offices, banks / ATMs / Easypaisa, drinking water, schools, mosques, bus stations).
After this, the map works from your own Flask server: fast, and it does not depend on the
busy public map servers.

Steps (Windows, in the project folder):
  1. py -m pip install pyshp
  2. Download (one file, a few hundred MB):
       https://download.geofabrik.de/asia/pakistan-latest-free.shp.zip
     and put it in the project folder (no need to unzip).
  3. py tools/build_help_places.py pakistan-latest-free.shp.zip
  4. Restart the server (python app.py).

Result:
  data/help_places.json  help places (only names and coordinates, no personal data)
  data/help_towns.json   every city, town and village of Pakistan, so the search box finds the
                         place on your own server at once (no waiting for an outside service)
"""
import json
import os
import re
import sys
import tempfile
import time
import zipfile

try:
    import shapefile  # pyshp
except ImportError:
    sys.exit("Please install pyshp first:  py -m pip install pyshp")

# ---------------------------------------------------------
# Which layers of the Geofabrik download we read
# (name inside the zip, without .shp)  ->  use names only?
# ---------------------------------------------------------
LAYERS = [
    ("gis_osm_pois_free_1", False),        # points: hospital, pharmacy, police, bank, atm ...
    ("gis_osm_pois_a_free_1", False),      # the same, drawn as areas
    ("gis_osm_pofw_free_1", False),        # places of worship (mosques)
    ("gis_osm_pofw_a_free_1", False),
    ("gis_osm_transport_free_1", False),   # bus / railway stations
    ("gis_osm_transport_a_free_1", False),
    ("gis_osm_buildings_a_free_1", True),  # buildings: only used to find names like "NADRA", "Edhi", "Dar ul Aman"
]
TOWN_LAYERS = ["gis_osm_places_free_1", "gis_osm_places_a_free_1"]   # cities, towns, villages
TOWN_RANK = {"national_capital": 0, "city": 1, "town": 2, "suburb": 3, "village": 4,
             "hamlet": 5, "locality": 6, "farm": 7, "isolated_dwelling": 7}

# ---------------------------------------------------------
# Names that decide the category (English + Urdu), checked first
# ---------------------------------------------------------
def rx(p):
    return re.compile(p, re.I)

NAME_FIRST = [
    ("nadra", rx(r"nadra|نادرا")),
    ("women", rx(r"dar.?ul.?aman|دار ?الامان|women protection|violence against women|women crisis|crisis cent(er|re)")),
    ("mental", rx(r"psychiatr|mental health|psycholog|ذہنی صحت|نفسیات")),
]
FCLASS = {
    "hospital": "health", "clinic": "health", "doctors": "health",
    "pharmacy": "pharmacy", "chemist": "pharmacy",
    "police": "police",
    "fire_station": "rescue",
    "bank": "bank", "atm": "bank",
    "drinking_water": "water",
    "town_hall": "govt", "courthouse": "govt",
    "school": "school", "college": "school",
    "bus_station": "transport", "railway_station": "transport",
}
NAME_LATER = [
    ("rescue", rx(r"rescue ?1122|ریسکیو|ambulance|ایمبولینس")),
    ("food", rx(r"dastarkhwan|دسترخوان|langar|لنگر")),
    ("shelter", rx(r"panah ?gah|پناہ ?گاہ|relief camp|ریلیف کیمپ|tent city|idp camp")),
    ("govt", rx(r"deputy commissioner|dc office|assistant commissioner|tehsil office|pdma|ڈپٹی کمشنر|اسسٹنٹ کمشنر")),
    ("bank", rx(r"easy ?paisa|jazz ?cash|ایزی ?پیسہ|جاز ?کیش|\bbisp\b|بی آئی ایس پی")),
    ("ngo", rx(r"\bngo\b|foundation|welfare|edhi|ایدھی|saylani|سیلانی|alkhidmat|al-khidmat|الخدمت|akhuwat|اخوت|chhipa|"
               r"unhcr|unicef|\bwfp\b|\biom\b|red crescent|save the children|islamic relief|\bhands\b|\bsrsp\b|\bnrsp\b|"
               r"فاؤنڈیشن|ویلفیئر")),
    ("water", rx(r"filter plant|filtration plant|فلٹر پلانٹ")),
]
# a quick test before the slower checks: does the name contain any interesting word?
ANY_NAME = rx("|".join(p.pattern for _, p in NAME_FIRST + NAME_LATER))


def category(fclass, name, names_only):
    fclass = (fclass or "").lower()
    if name:
        for cat, pat in NAME_FIRST:
            if pat.search(name):
                return cat
    if not names_only:
        if fclass.startswith("muslim"):
            return "mosque"
        if fclass in FCLASS:
            return FCLASS[fclass]
    if name:
        for cat, pat in NAME_LATER:
            if pat.search(name):
                return cat
    return None


def centre(shape):
    if shape.shapeType in (shapefile.POINT, shapefile.POINTZ, shapefile.POINTM) and shape.points:
        x, y = shape.points[0]
    else:
        x1, y1, x2, y2 = shape.bbox
        x, y = (x1 + x2) / 2, (y1 + y2) / 2
    return round(y, 5), round(x, 5)   # lat, lon


def read_layer(folder, layer, names_only, out, seen):
    path = os.path.join(folder, layer + ".shp")
    if not os.path.exists(path):
        print(f"  (skipped {layer}: not in the download)")
        return 0
    added = 0
    t0 = time.time()
    with shapefile.Reader(path, encoding="utf-8", encodingErrors="replace") as sf:
        fields = [f[0] for f in sf.fields[1:]]
        i_id, i_fc, i_name = fields.index("osm_id"), fields.index("fclass"), fields.index("name")
        for n, rec in enumerate(sf.iterRecords(fields=None)):
            name = (rec[i_name] or "").strip()
            if names_only and (not name or not ANY_NAME.search(name)):
                continue
            cat = category(rec[i_fc], name, names_only)
            if not cat:
                continue
            # node ids and way ids can be the same number, so keep points and areas apart
            key = ("a" if "_a_" in layer else "n") + str(rec[i_id])
            if key in seen:
                continue
            seen.add(key)
            lat, lon = centre(sf.shape(n))
            out.append([lat, lon, cat, name])
            added += 1
            if added % 20000 == 0:
                print(f"    … {added} places so far")
    print(f"  {layer}: {added} places ({time.time() - t0:.0f}s)")
    return added


def read_towns(folder):
    """Every named city / town / village: [name, lat, lon, rank, population, nearest town]."""
    towns, seen = [], set()
    for layer in TOWN_LAYERS:
        path = os.path.join(folder, layer + ".shp")
        if not os.path.exists(path):
            print(f"  (skipped {layer}: not in the download)")
            continue
        with shapefile.Reader(path, encoding="utf-8", encodingErrors="replace") as sf:
            fields = [f[0] for f in sf.fields[1:]]
            i_fc, i_name = fields.index("fclass"), fields.index("name")
            i_pop = fields.index("population") if "population" in fields else None
            for n, rec in enumerate(sf.iterRecords()):
                name = (rec[i_name] or "").strip()
                rank = TOWN_RANK.get((rec[i_fc] or "").lower())
                if not name or rank is None:
                    continue
                lat, lon = centre(sf.shape(n))
                key = (name.lower(), round(lat, 2), round(lon, 2))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    pop = int(rec[i_pop] or 0) if i_pop is not None else 0
                except (TypeError, ValueError):
                    pop = 0
                towns.append([name, lat, lon, rank, pop, ""])
    # for villages and suburbs: the nearest city or town, to tell same-named places apart
    grid = {}
    for t in towns:
        if t[3] <= 2:
            grid.setdefault((int(t[1]), int(t[2])), []).append(t)
    for t in towns:
        if t[3] <= 2:
            continue
        best, bd = None, 9e9
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for c in grid.get((int(t[1]) + dx, int(t[2]) + dy), ()):
                    d = (c[1] - t[1]) ** 2 + (c[2] - t[2]) ** 2
                    if d < bd:
                        best, bd = c, d
        if best and best[0] != t[0]:
            t[5] = best[0]
    print(f"  towns and villages: {len(towns)}")
    return towns


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_path = os.path.join(project, "data", "help_places.json")

    tmp = None
    folder = src
    if src.lower().endswith(".zip"):
        tmp = tempfile.mkdtemp(prefix="help_places_")
        print("Unzipping the layers we need …")
        with zipfile.ZipFile(src) as z:
            wanted = {l for l, _ in LAYERS} | set(TOWN_LAYERS)
            for member in z.namelist():
                base = os.path.splitext(os.path.basename(member))[0]
                if base in wanted:
                    z.extract(member, tmp)
                    # keep files flat in the temp folder
                    extracted = os.path.join(tmp, member)
                    flat = os.path.join(tmp, os.path.basename(member))
                    if extracted != flat:
                        os.replace(extracted, flat)
        folder = tmp

    places, seen = [], set()
    print("Reading OpenStreetMap layers …")
    for layer, names_only in LAYERS:
        read_layer(folder, layer, names_only, places, seen)

    counts = {}
    for p in places:
        counts[p[2]] = counts.get(p[2], 0) + 1
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"source": "OpenStreetMap contributors (Geofabrik extract)",
                   "built": time.strftime("%Y-%m-%d"),
                   "fields": ["lat", "lon", "cat", "name"],
                   "places": places}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\nSaved {len(places)} places to {out_path} ({os.path.getsize(out_path) / 1e6:.1f} MB)")
    for k in sorted(counts, key=counts.get, reverse=True):
        print(f"  {k:10s} {counts[k]}")

    print("\nReading cities, towns and villages …")
    towns = read_towns(folder)
    towns_path = os.path.join(project, "data", "help_towns.json")
    with open(towns_path, "w", encoding="utf-8") as f:
        json.dump({"source": "OpenStreetMap contributors (Geofabrik extract)",
                   "fields": ["name", "lat", "lon", "rank", "population", "near"],
                   "places": towns}, f, ensure_ascii=False, separators=(",", ":"))
    print(f"Saved {len(towns)} cities/towns/villages to {towns_path} ({os.path.getsize(towns_path) / 1e6:.1f} MB)")

    if tmp:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nDone. Restart the server (python app.py) and open /resources.")


if __name__ == "__main__":
    main()
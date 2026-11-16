"""Build the Practical Help map data from the FULL OpenStreetMap file of Pakistan (run ONCE).

Why this file and not the .shp.zip: the shapefile download leaves out offices, so most
NADRA centres, DC offices, NGOs, welfare offices, Dar-ul-Aman etc. were missing.
The .osm.pbf file has every tag, so this script finds all of them (with Urdu names and
phone numbers where they are listed).

Steps (Windows, in the project folder):
  1. py -m pip install osmium
  2. Download (one file, about 200-300 MB) in the browser:
       https://download.geofabrik.de/asia/pakistan-latest.osm.pbf
     and put it in the project folder.
  3. py tools/build_help_places_pbf.py pakistan-latest.osm.pbf
     (takes a few minutes; it prints its progress)
  4. Restart the server (python app.py).

Result (same format the server already reads, nothing else to change):
  data/help_places.json  [lat, lon, cat, name, name_ur, phone]
  data/help_towns.json   every city, town and village (English AND Urdu names)
Only public places: names, coordinates and public phone numbers. No personal data.
"""
import json
import os
import re
import sys
import time

try:
    import osmium
except ImportError:
    sys.exit("Please install osmium first:  py -m pip install osmium")


def rx(p):
    return re.compile(p, re.I)


# ---------------------------------------------------------
# NAMES (English, Roman Urdu, Urdu) -> category
# ---------------------------------------------------------
N_NADRA = rx(r"nadra|n\.a\.d\.r\.a|نادرا|\bnrc\b")
N_WOMEN = rx(r"dar.?ul.?aman|دار ?الامان|women protection|violence against women|women crisis|crisis cent(er|re)|shaheed benazir bhutto cent")
N_MENTAL = rx(r"psychiatr|mental health|psycholog|ذہنی صحت|نفسیات|rehabilitation cent")
N_RESCUE = rx(r"rescue ?1122|\b1122\b|ریسکیو|ambulance|ایمبولینس|edhi|ایدھی|chhipa|چھیپا|fire ?brigade|fire station|فائر")
N_POLICE = rx(r"police|thana|thaana|پولیس|تھانہ|levies|rangers? (post|check)")
N_FOOD = rx(r"dastar ?khwan|دسترخوان|langar|لنگر|saylani|سیلانی|food bank|ration point")
N_SHELTER = rx(r"panah ?gah|پناہ ?گاہ|relief camp|ریلیف کیمپ|tent city|idp camp|refugee camp|shelter home|flood camp")
N_PHARMACY = rx(r"pharmac|chemist|medical store|medicose|medicos|drug store|فارمیسی|میڈیکل اسٹور|دواخانہ")
N_HEALTH = rx(r"hospital|clinic|\bbhu\b|\brhc\b|\bthq\b|\bdhq\b|dispensary|health cent|maternity|ہسپتال|اسپتال|کلینک|شفاخانہ|ڈسپنسری|مرکز صحت")
N_GOVT = rx(r"deputy commissioner|\bdc office|assistant commissioner|\bac office|commissioner office|tehsil|tahsil|\bpdma\b|\bndma\b|"
            r"district council|municipal|town committee|union council|social welfare|bait.?ul.?mal|zakat|\bbisp\b|benazir income|"
            r"ڈپٹی کمشنر|اسسٹنٹ کمشنر|تحصیل|بیت المال|زکوٰۃ|سوشل ویلفیئر")
N_BANK = rx(r"\bbank\b|\batm\b|easy ?paisa|jazz ?cash|ایزی ?پیسہ|جاز ?کیش|بینک|\bhbl\b|\bubl\b|\bmcb\b|meezan|allied bank|"
            r"bank al.?habib|alfalah|askari|faysal|js bank|national bank|\bnbp\b|bank islami|dubai islamic|soneri|silk ?bank|summit bank|sindh bank|bank of punjab|\bbop\b|\bkhyber bank")
N_WATER = rx(r"filter plant|filtration plant|فلٹر پلانٹ|water plant|ro plant|clean drinking water")
N_NGO = rx(r"\bngo\b|foundation|welfare|\btrust\b|alkhidmat|al-khidmat|الخدمت|akhuwat|اخوت|unhcr|unicef|\bwfp\b|\biom\b|"
           r"red crescent|save the children|islamic relief|\bhands\b|\bsrsp\b|\bnrsp\b|\btrdp\b|\bsprdp\b|\brspn\b|aga khan|"
           r"concern worldwide|oxfam|care international|world vision|\bmsf\b|doctors without borders|acted|plan international|"
           r"فاؤنڈیشن|ویلفیئر|ٹرسٹ")


def category(t, name):
    """t: dict of OSM tags. Returns one category or None.
    1) very specific names (NADRA, Dar-ul-Aman, mental health)  2) the OSM tags  3) the name alone."""
    a = t.get("amenity", "")
    hc = t.get("healthcare", "")
    office = t.get("office", "")
    sf = t.get("social_facility", "")
    # 1) names that always decide
    if N_NADRA.search(name):
        return "nadra"
    if N_WOMEN.search(name) or re.search(r"women|abused|victim", t.get("social_facility:for", "")):
        return "women"
    if re.search(r"psychotherapist|counselling|psychiatrist|psychologist", hc) or \
            re.search(r"psychiatry|psychology", t.get("healthcare:speciality", "")) or N_MENTAL.search(name):
        return "mental"
    # 2) the map tags
    if a == "fire_station" or t.get("emergency") == "ambulance_station":
        return "rescue"
    if a == "police":
        return "police"
    if a == "food_bank" or re.search(r"food_bank|soup_kitchen", sf):
        return "food"
    if sf == "shelter" or a == "refugee_site" or t.get("emergency") == "assembly_point":
        return "shelter"
    if a == "pharmacy" or t.get("shop") == "chemist" or hc == "pharmacy":
        return "pharmacy"
    if a in ("hospital", "clinic", "doctors") or hc in ("hospital", "clinic", "centre", "doctor"):
        return "health"
    if a in ("bank", "atm", "money_transfer", "bureau_de_change"):
        return "bank"
    if a in ("drinking_water", "water_point") or t.get("man_made") == "water_tap":
        return "water"
    if office == "government" or a in ("townhall", "courthouse"):
        return "govt"
    if office in ("ngo", "association", "charity", "foundation", "non_profit", "humanitarian", "international_organization") \
            or a == "social_facility":
        return "ngo"
    if a in ("school", "college", "university"):
        return "school"
    if a == "place_of_worship":
        return "mosque" if t.get("religion", "muslim") == "muslim" else None
    if a == "bus_station" or t.get("railway") in ("station", "halt") or t.get("public_transport") == "station":
        return "transport"
    # 3) only a name (buildings, offices and shops without a type)
    if not name:
        return None
    for cat, pat in (("rescue", N_RESCUE), ("police", N_POLICE), ("food", N_FOOD), ("shelter", N_SHELTER),
                     ("pharmacy", N_PHARMACY), ("health", N_HEALTH), ("water", N_WATER), ("govt", N_GOVT),
                     ("bank", N_BANK), ("ngo", N_NGO)):
        if pat.search(name):
            return cat
    return None


# tags worth looking at (everything else is skipped quickly)
KEYS = ("name", "amenity", "healthcare", "office", "social_facility", "emergency", "shop", "man_made",
        "railway", "public_transport", "place")

TOWN_RANK = {"city": 1, "town": 2, "suburb": 3, "village": 4, "hamlet": 5, "neighbourhood": 5, "locality": 6,
             "isolated_dwelling": 7, "farm": 7}
SKIP_NAME_ONLY = {"highway", "waterway", "natural", "landuse", "boundary", "power", "route", "barrier", "leisure"}


def centre(obj):
    if isinstance(obj, osmium.osm.Node):
        loc = obj.location
        return (round(loc.lat, 5), round(loc.lon, 5)) if loc.valid() else None
    lats, lons = [], []
    for n in obj.nodes:
        if n.location.valid():
            lats.append(n.location.lat)
            lons.append(n.location.lon)
    if not lats:
        return None
    return round((min(lats) + max(lats)) / 2, 5), round((min(lons) + max(lons)) / 2, 5)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(project, "data")
    os.makedirs(data_dir, exist_ok=True)

    places, towns, seen = [], [], set()
    t0, n = time.time(), 0
    print("Reading", src, "… (a few minutes for all of Pakistan)")
    fp = osmium.FileProcessor(src, osmium.osm.NODE | osmium.osm.WAY).with_locations() \
        .with_filter(osmium.filter.KeyFilter(*KEYS))
    for obj in fp:
        n += 1
        if n % 200000 == 0:
            print(f"  … {n} tagged objects read, {len(places)} help places so far ({time.time() - t0:.0f}s)")
        t = {tag.k: tag.v for tag in obj.tags}
        name = (t.get("name") or t.get("name:en") or t.get("name:ur") or "").strip()
        name_ur = (t.get("name:ur") or "").strip()

        # cities, towns, villages (English and Urdu names both searchable)
        place = t.get("place")
        if place in TOWN_RANK or (place == "city" and t.get("capital") in ("yes", "2")):
            c = centre(obj)
            if c and name:
                rank = 0 if t.get("capital") in ("yes", "2") and place == "city" else TOWN_RANK[place]
                try:
                    pop = int(re.sub(r"[^\d]", "", t.get("population", "")) or 0)
                except ValueError:
                    pop = 0
                for nm in {name, t.get("name:en", "").strip(), name_ur}:
                    if nm:
                        towns.append([nm, c[0], c[1], rank, pop, ""])
            continue

        # roads, rivers, fields ... with a matching word in the name are not help places
        if not any(k in t for k in ("amenity", "healthcare", "office", "social_facility", "emergency", "shop",
                                    "man_made", "railway", "public_transport", "building")) and \
                any(k in t for k in SKIP_NAME_ONLY):
            continue
        cat = category(t, " ".join(x for x in (name, t.get("name:en", ""), name_ur) if x))
        if not cat:
            continue
        c = centre(obj)
        if not c:
            continue
        key = (cat, name.lower(), round(c[0], 3), round(c[1], 3))   # same place drawn twice (point + building)
        if key in seen:
            continue
        seen.add(key)
        phone = (t.get("phone") or t.get("contact:phone") or t.get("contact:mobile") or "").split(";")[0].strip()[:30]
        places.append([c[0], c[1], cat, name, name_ur if name_ur != name else "", phone])

    # nearest city / town for villages, to tell same-named places apart
    grid = {}
    urdu = re.compile(r"[\u0600-\u06ff]")
    for tw in towns:
        if tw[3] <= 2 and not urdu.search(tw[0]):      # English names for "near ..."
            grid.setdefault((int(tw[1]), int(tw[2])), []).append(tw)
    for tw in towns:
        if tw[3] <= 2:
            continue
        best, bd = None, 9e9
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for cc in grid.get((int(tw[1]) + dx, int(tw[2]) + dy), ()):
                    d = (cc[1] - tw[1]) ** 2 + (cc[2] - tw[2]) ** 2
                    if d < bd:
                        best, bd = cc, d
        if best and best[0] != tw[0]:
            tw[5] = best[0]

    meta = {"source": "OpenStreetMap contributors (Geofabrik extract)", "built": time.strftime("%Y-%m-%d")}
    p_path = os.path.join(data_dir, "help_places.json")
    with open(p_path, "w", encoding="utf-8") as f:
        json.dump(dict(meta, fields=["lat", "lon", "cat", "name", "name_ur", "phone"], places=places),
                  f, ensure_ascii=False, separators=(",", ":"))
    t_path = os.path.join(data_dir, "help_towns.json")
    with open(t_path, "w", encoding="utf-8") as f:
        json.dump(dict(meta, fields=["name", "lat", "lon", "rank", "population", "near"], places=towns),
                  f, ensure_ascii=False, separators=(",", ":"))

    counts = {}
    for p in places:
        counts[p[2]] = counts.get(p[2], 0) + 1
    print(f"\nSaved {len(places)} help places to {p_path} ({os.path.getsize(p_path) / 1e6:.1f} MB)")
    for k in sorted(counts, key=counts.get, reverse=True):
        print(f"  {k:10s} {counts[k]}")
    print(f"Saved {len(towns)} city/town/village names to {t_path} ({os.path.getsize(t_path) / 1e6:.1f} MB)")
    print(f"\nDone in {time.time() - t0:.0f}s. Restart the server (python app.py) and open /resources.")


if __name__ == "__main__":
    main()
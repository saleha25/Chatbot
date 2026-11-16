"""Practical Help page: verified helplines, simple steps, and the "Find help near you" map.

No API key is used anywhere, and the search never waits for a busy outside server:
  - data/help_towns.json  : every city, town and village of Pakistan  -> /api/find_place
  - data/help_places.json : hospitals, NADRA, banks, NGOs ...           -> /api/help_places
Both are built once with tools/build_help_places.py from OpenStreetMap (Geofabrik).
Every place gets a plain Google Maps link in the browser, and every category a
"see all on Google Maps" link, so even a category with no saved places is never empty.
It does not touch the chat or any other feature."""
import difflib
import json
import os
import re
import threading
import unicodedata

from flask import jsonify, render_template, request

from .core import app, login_required, PROJECT_DIR

HELP_PLACES_PATH = os.path.join(PROJECT_DIR, "data", "help_places.json")
HELP_TOWNS_PATH = os.path.join(PROJECT_DIR, "data", "help_towns.json")
_places = None            # loaded once, kept in memory
_places_lock = threading.Lock()
MAX_PER_CATEGORY = 400


def _load_places():
    global _places
    if _places is not None:
        return _places
    with _places_lock:
        if _places is None:
            try:
                with open(HELP_PLACES_PATH, encoding="utf-8") as f:
                    _places = json.load(f).get("places", [])
                print(f"Help places loaded: {len(_places)}")
            except FileNotFoundError:
                _places = []
            except Exception as e:
                print("Help places load error:", e)
                _places = []
    return _places


@app.route("/resources")
@login_required
def resources():
    return render_template("resources.html")


@app.route("/api/help_places")
@login_required
def help_places():
    """Places inside a box: /api/help_places?s=..&w=..&n=..&e=..&cats=health,bank
    Answer: {"available": true, "places": [[lat, lon, cat, name], ...]}"""
    places = _load_places()
    if not places:
        return jsonify({"available": False, "places": []})
    try:
        s, w = float(request.args["s"]), float(request.args["w"])
        n, e = float(request.args["n"]), float(request.args["e"])
    except (KeyError, ValueError):
        return jsonify({"available": True, "places": [], "error": "bad box"}), 400
    cats = {c for c in (request.args.get("cats") or "").split(",") if c}

    # keep the places nearest to the middle of the box when there are very many
    mid_lat, mid_lon = (s + n) / 2, (w + e) / 2
    found = {}
    for p in places:
        lat, lon, cat = p[0], p[1], p[2]
        if s <= lat <= n and w <= lon <= e and (not cats or cat in cats):
            found.setdefault(cat, []).append(p)
    out = []
    for cat, items in found.items():
        if len(items) > MAX_PER_CATEGORY:
            items.sort(key=lambda p: (p[0] - mid_lat) ** 2 + (p[1] - mid_lon) ** 2)
            items = items[:MAX_PER_CATEGORY]
        out.extend(items)
    return jsonify({"available": True, "places": out})


# ─────────────────────────────────────────────
# CITY / TOWN / VILLAGE SEARCH (our own data, instant)
# ─────────────────────────────────────────────
_towns = None             # [[name, lat, lon, rank, population, near], ...]
_towns_norm = None        # normalised names, same order
_towns_big = None         # {normalised name: index} for cities, towns and suburbs (spelling mistakes)
_towns_lock = threading.Lock()
RANK_LABEL = {0: ("Capital", "دارالحکومت"), 1: ("City", "شہر"), 2: ("Town", "قصبہ"), 3: ("Area", "علاقہ"),
              4: ("Village", "گاؤں"), 5: ("Village", "گاؤں"), 6: ("Place", "جگہ"), 7: ("Place", "جگہ")}
RANK_RADIUS = {0: 15000, 1: 12000, 2: 6000, 3: 3000, 4: 4000}   # metres; others 2500


def _norm(t):
    t = unicodedata.normalize("NFKC", (t or "")).lower()
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")   # Urdu zer/zabar etc.
    t = re.sub(r"[^\w\s]", " ", t)          # keeps Urdu letters, drops punctuation
    return re.sub(r"\s+", " ", t).strip()


def _load_towns():
    global _towns, _towns_norm, _towns_big
    if _towns is not None:
        return _towns
    with _towns_lock:
        if _towns is None:
            try:
                with open(HELP_TOWNS_PATH, encoding="utf-8") as f:
                    towns = json.load(f).get("places", [])
                print(f"Help towns loaded: {len(towns)}")
            except FileNotFoundError:
                towns = []
            except Exception as e:
                print("Help towns load error:", e)
                towns = []
            _towns_norm = [_norm(t[0]) for t in towns]
            _towns_big = {}
            for i, t in enumerate(towns):
                if t[3] <= 3:
                    _towns_big.setdefault(_towns_norm[i].replace(" ", ""), i)
            _towns = towns
    return _towns


@app.route("/api/find_place")
@login_required
def find_place():
    """/api/find_place?q=umerkot&lang=en
    Answer: {"available": true, "results": [{"name", "sub", "lat", "lon", "r"}, ...]}  (best first)"""
    towns = _load_towns()
    if not towns:
        return jsonify({"available": False, "results": []})
    q = _norm(request.args.get("q", ""))[:60]
    ur = request.args.get("lang") == "ur"
    try:
        limit = max(1, min(int(request.args.get("limit", 6)), 10))
    except ValueError:
        limit = 6
    if len(q) < 2:
        return jsonify({"available": True, "results": []})
    qs = q.replace(" ", "")

    scored = []
    for i, nm in enumerate(_towns_norm):
        if qs not in nm.replace(" ", "") and q not in nm:
            continue
        flat = nm.replace(" ", "")
        if flat == qs:
            score = 0
        elif flat.startswith(qs):
            score = 1
        elif any(w.startswith(q) for w in nm.split(" ")):
            score = 2
        elif len(qs) >= 3:
            score = 3
        else:
            continue
        t = towns[i]
        scored.append((score, t[3], -t[4], i))

    if not scored:
        # spelling mistakes ("umarkot", "mithy"): closest city / town / area names
        for m in difflib.get_close_matches(qs, _towns_big.keys(), n=limit, cutoff=0.75):
            i = _towns_big[m]
            scored.append((4, towns[i][3], -towns[i][4], i))

    scored.sort()
    out, seen = [], set()
    for _, _, _, i in scored:
        name, lat, lon, rank, _pop, near = towns[i]
        key = (_towns_norm[i], near, round(lat, 1), round(lon, 1))
        if key in seen:
            continue
        seen.add(key)
        label = RANK_LABEL.get(rank, ("Place", "جگہ"))[1 if ur else 0]
        sub = label + (" · " + ("قریب " if ur else "near ") + near if near else "") + (" · پاکستان" if ur else " · Pakistan")
        out.append({"name": name, "sub": sub, "lat": lat, "lon": lon, "r": RANK_RADIUS.get(rank, 2500)})
        if len(out) >= limit:
            break
    return jsonify({"available": True, "results": out})


# load both files in the background as soon as the server starts, so the first search is instant too
threading.Thread(target=lambda: (_load_places(), _load_towns()), daemon=True).start()
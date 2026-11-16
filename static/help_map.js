/* =========================================================
   EMPATH AI — FIND HELP NEAR YOU (Practical Help page)

   Works from the Empath AI server's own data, so typing a place shows results at once
   and nothing depends on a busy public server. No API key anywhere.
     - /api/find_place  : every city, town and village of Pakistan (data/help_towns.json)
     - /api/help_places : hospitals, NADRA, banks, NGOs ... (data/help_places.json)
     - Backup only for place names not in our data: Nominatim + Photon (free, no key)
     - Map: OpenStreetMap tiles. Route on the map: OSRM.
     - Google Maps: plain links for every place, navigation and "see all" (no key)
   The user's location stays in this page only. Nothing is sent to the Empath AI server.
   ========================================================= */
(function () {
  "use strict";

  const UR = document.documentElement.lang === "ur";
  const PK_BBOX = "60.8,23.6,77.9,37.1"; // Pakistan (west, south, east, north)

  // ---------------------------------------------------------
  // CATEGORIES (everything an IDP may need)
  //   essential: shown under "All". Others load when the user picks them.
  // ---------------------------------------------------------
  const CATS = {
    // g: what to search on Google Maps for this category ("see all on Google Maps")
    health:   { en: "Hospitals / clinics", ur: "ہسپتال / کلینک", color: "#0f766e", g: "hospitals", essential: true },
    pharmacy: { en: "Pharmacy", ur: "فارمیسی / میڈیکل اسٹور", color: "#16a34a", g: "pharmacy", essential: true },
    rescue:   { en: "Rescue / ambulance", ur: "ریسکیو / ایمبولینس", color: "#dc2626", g: "Rescue 1122", essential: true },
    police:   { en: "Police", ur: "پولیس", color: "#1d4ed8", g: "police station", essential: true },
    ngo:      { en: "NGOs / welfare", ur: "این جی اوز / فلاحی ادارے", color: "#9333ea", g: "NGO", essential: true },
    food:     { en: "Food / ration", ur: "کھانا / راشن", color: "#ca8a04", g: "free food dastarkhwan", essential: true },
    shelter:  { en: "Shelter / camps", ur: "پناہ گاہ / کیمپ", color: "#ea580c", g: "shelter panahgah", essential: true },
    women:    { en: "Women protection", ur: "خواتین کا تحفظ", color: "#db2777", g: "Dar ul Aman women protection centre", essential: true },
    mental:   { en: "Mental health", ur: "ذہنی صحت", color: "#be185d", g: "psychiatrist", essential: true },
    nadra:    { en: "NADRA", ur: "نادرا", color: "#7c3aed", g: "NADRA office", essential: true },
    govt:     { en: "DC / government offices", ur: "ڈی سی / سرکاری دفاتر", color: "#334155", g: "Deputy Commissioner office", essential: true },
    bank:     { en: "Bank / ATM / mobile money", ur: "بینک / اے ٹی ایم / موبائل منی", color: "#0369a1", g: "ATM", essential: true },
    water:    { en: "Drinking water", ur: "پینے کا پانی", color: "#0891b2", g: "water filter plant", essential: true },
    school:   { en: "Schools", ur: "اسکول", color: "#65a30d", g: "school", essential: false },
    mosque:   { en: "Mosques", ur: "مساجد", color: "#15803d", g: "mosque", essential: false },
    transport:{ en: "Bus / transport", ur: "بس اڈہ / ٹرانسپورٹ", color: "#a16207", g: "bus stand", essential: false }
  };
  const CAT_KEYS = Object.keys(CATS);

  // one small picture per category: used on the map pins, the boxes and the "nearest help" tiles
  const ICON = {
    health: "M12 6v12M6 12h12",
    pharmacy: "M10.5 19.5l-6-6a4.24 4.24 0 0 1 6-6l6 6a4.24 4.24 0 0 1-6 6zM7.5 10.5l6 6",
    rescue: "M12 3v3M5 7l2 2M19 7l-2 2M7 20v-5a5 5 0 0 1 10 0v5M4 20h16",
    police: "M12 3l7 3v5c0 5-3.5 8-7 10-3.5-2-7-5-7-10V6z",
    ngo: "M12 20s-7-4.5-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.5-7 10-7 10z",
    food: "M4 11h16a8 8 0 0 1-16 0zM9 7.5c0-1 1-1.5 0-3M14 7.5c0-1 1-1.5 0-3",
    shelter: "M3 20L12 4l9 16zM12 20v-6",
    women: "M12 13.5a4.75 4.75 0 1 0 0-9.5 4.75 4.75 0 0 0 0 9.5zM12 13.5V21M9 18h6",
    mental: "M4 5h16v10H9l-5 4z",
    nadra: "M3 6h18v12H3zM7 10h4M7 14h6M15 10h2.5",
    govt: "M3 9l9-5 9 5M5.5 10v8M10 10v8M14 10v8M18.5 10v8M3 20h18",
    bank: "M3 7h18v10H3zM12 14a2 2 0 1 0 0-4 2 2 0 0 0 0 4z",
    water: "M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z",
    school: "M2 9l10-5 10 5-10 5zM6 11v5c3 2 9 2 12 0v-5",
    mosque: "M5 20v-7a7 7 0 0 1 14 0v7zM12 3v3M3 20h18",
    transport: "M5 4h14v12H5zM5 11h14M8 19v-3M16 19v-3"
  };
  function catSvg(k, cls) {
    return '<svg class="' + (cls || "ci") + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" ' +
           'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="' + (ICON[k] || ICON.ngo) + '"/></svg>';
  }

  // Words people type (English, Urdu, Roman Urdu) -> category
  const KEYWORDS = [
    ["ngo", /\b(ngos?|charit(y|ies)|welfare|foundations?|trusts?|relief org\w*|fala?hi)\b|این جی او|فلاحی|ویلفیئر|خیراتی/i],
    ["health", /\b(hospitals?|clinics?|doctors?|dispensar(y|ies)|bhu|rhc|haspatal|aspatal|dawakhana)\b|ہسپتال|اسپتال|کلینک|ڈاکٹر|شفاخانہ/i],
    ["pharmacy", /\b(pharmac(y|ies)|chemists?|medical stores?|medicines?|dawai|dawa)\b|فارمیسی|میڈیکل|دوائی|دوا/i],
    ["rescue", /\b(rescue|ambulances?|1122|edhi|chhipa|fire ?(brigade|station))\b|ریسکیو|ایمبولینس|ایدھی|فائر/i],
    ["police", /\b(police|thana|thaana|police station)\b|پولیس|تھانہ/i],
    ["food", /\b(food|ration|langar|dastarkhwan|khana|roti|saylani)\b|کھانا|راشن|لنگر|دسترخوان/i],
    ["shelter", /\b(shelters?|camps?|panah ?gah|tents?|stay|rehne ki jagah)\b|پناہ|کیمپ|خیمہ/i],
    ["women", /\b(women|darul ?aman|dar ul aman|aurat|khawateen|domestic violence)\b|خواتین|دارالامان|عورت/i],
    ["mental", /\b(mental|psychiatrists?|psycholog\w*|counsell?ing|therap\w*)\b|ذہنی|نفسیات/i],
    ["nadra", /\b(nadra|cnic|id card|shanakhti)\b|نادرا|شناختی/i],
    ["govt", /\b(dc office|deputy commissioner|government|sarkari|tehsil|pdma|commissioner)\b|سرکاری|ڈپٹی کمشنر|تحصیل/i],
    ["bank", /\b(banks?|atms?|easy ?paisa|jazz ?cash|bisp|money|paise|cash)\b|بینک|اے ٹی ایم|ایزی پیسہ|جاز کیش|پیسے/i],
    ["water", /\b(water|pani|paani|filter plant)\b|پانی/i],
    ["school", /\b(schools?|colleges?|madrasa)\b|اسکول|سکول|کالج|مدرسہ/i],
    ["mosque", /\b(mosques?|masjid)\b|مسجد|مساجد/i],
    ["transport", /\b(bus|adda|transport|station|train)\b|بس|اڈہ|ٹرانسپورٹ/i]
  ];
  const FILLER = /\b(in|near|nearby|around|me|my|mein|main|me|ke|ki|ka|qareeb|kareeb|paas|pass|find|show|the|of|for|all|list)\b|میں|کے|کی|کا|قریب|پاس/gi;

  const T = UR ? {
    finding: "علاقہ تلاش ہو رہا ہے…",
    searching: (a) => `${a} میں مدد کی جگہیں تلاش ہو رہی ہیں…`,
    notFound: "یہ جگہ نہیں ملی۔ شہر، قصبے یا گاؤں کا نام انگریزی یا اردو میں لکھیں۔",
    typeFirst: "پہلے شہر یا علاقے کا نام لکھیں، یا \"میری لوکیشن\" دبائیں۔",
    gpsAsk: "لوکیشن لی جا رہی ہے…",
    gpsDenied: "لوکیشن کی اجازت نہیں ملی۔ شہر کا نام لکھ کر تلاش کریں۔",
    gpsInsecure: "یہ بٹن صرف https یا اسی کمپیوٹر (localhost) پر چلتا ہے۔ شہر کا نام لکھ کر تلاش کریں۔",
    netErr: "یہ جگہ ابھی نہیں مل سکی۔ قریبی شہر کا نام لکھ کر دیکھیں۔",
    found: (n, a, from) => `${a}: ${n} جگہیں ملیں — ${from} سے فاصلے کے لحاظ سے۔`,
    fromYou: "آپ کی لوکیشن", fromCentre: "مرکز",
    none: (c, a) => `${a} میں ${c} کی کوئی جگہ نقشے کے ڈیٹا میں درج نہیں۔ نیچے گوگل میپس پر دیکھیں۔`,
    noneAll: (a) => `${a} میں اس فاصلے کے اندر نقشے کے ڈیٹا میں کوئی جگہ درج نہیں۔ زیادہ فاصلہ چنیں یا نیچے گوگل میپس دیکھیں۔`,
    tooBig: "یہ علاقہ بہت بڑا ہے، اس لیے اس کے مرکز سے 25 کلومیٹر دکھائے جا رہے ہیں۔ بہتر نتائج کے لیے شہر کا نام لکھیں۔",
    you: "آپ یہاں ہیں", km: "کلومیٹر", m: "میٹر",
    route: "راستہ", nav: "نیویگیشن", call: "کال", web: "ویب سائٹ",
    routing: "راستہ بن رہا ہے…", routeInfo: (n, d, t) => `${n} · گاڑی سے تقریباً ${d}، ${t} منٹ`,
    routeErr: "راستہ نہیں بن سکا۔ \"نیویگیشن\" دبا کر گوگل میپس میں دیکھیں۔",
    routeNeedsGps: "نقشے پر راستہ دیکھنے کے لیے پہلے \"میری لوکیشن\" دبائیں، یا \"نیویگیشن\" سے گوگل میپس کھولیں۔",
    unnamed: "نام درج نہیں", all: "سب ضروری", more: (n) => `مزید ${n} دکھائیں`,
    google: (c, a) => `گوگل میپس پر "${c}، ${a}" دیکھیں`,
    filter: "نام سے تلاش کریں…", hours: "اوقات", whole: "پورا علاقہ",
    gmaps: "گوگل میپس", seeAllG: (c) => `گوگل میپس پر سب ${c}`, onMap: "نقشے پر دیکھیں",
    catNone: "نقشے کے ڈیٹا میں یہاں درج نہیں۔ گوگل میپس پر دیکھیں:",
    catMore: (n) => `+ ${n} مزید (نقشے پر)`, near: "قریب",
    nearestOf: (c) => `قریب ترین: ${c}`, nearestTitle: (a, f) => `${a} میں سب سے قریب مدد (${f} سے)`, sos: "ایمرجنسی: 1122",
    emptyRow: (a) => `${a} کے لیے یہ زمرے ہمارے نقشے کے ڈیٹا میں درج نہیں۔ گوگل میپس پر دیکھنے کے لیے دبائیں:`,
    noData: (a) => `${a}: ہر زمرے میں "گوگل میپس پر سب" دبا کر وہاں کی جگہیں دیکھیں۔`,
    widened: (a, r) => `${a} میں کم جگہیں درج ہیں، اس لیے ${r} کلومیٹر کے اندر قریب ترین مدد دکھائی جا رہی ہے۔ ہر زمرے میں "گوگل میپس پر سب" سے مزید دیکھیں۔`
  } : {
    finding: "Finding the place…",
    searching: (a) => `Looking for help in ${a}…`,
    notFound: "We could not find this place. Type a city, town or village name in English or Urdu.",
    typeFirst: "Type a city or area first, or tap \"Use my location\".",
    gpsAsk: "Getting your location…",
    gpsDenied: "Location permission was not given. Type your city instead.",
    gpsInsecure: "This button works only on https or on this computer (localhost). Type your city instead.",
    netErr: "Could not find this place right now. Try the nearest city name.",
    found: (n, a, from) => `${a}: ${n} places found, sorted by distance from ${from}.`,
    fromYou: "your location", fromCentre: "the centre",
    none: (c, a) => `No ${c} listed in ${a} in the map data. Check Google Maps below.`,
    noneAll: (a) => `No places listed within this distance in ${a} in the map data. Choose a bigger distance or check Google Maps below.`,
    tooBig: "This area is very large, so 25 km around its centre is shown. Search a city for better results.",
    you: "You are here", km: "km", m: "m",
    route: "Route", nav: "Directions", call: "Call", web: "Website",
    routing: "Making the route…", routeInfo: (n, d, t) => `${n} · about ${d} by car, ${t} min`,
    routeErr: "Could not make the route. Tap \"Directions\" to open Google Maps.",
    routeNeedsGps: "To draw a route on the map, tap \"Use my location\" first, or tap \"Directions\" to open Google Maps.",
    unnamed: "Name not listed", all: "All essentials", more: (n) => `Show ${n} more`,
    google: (c, a) => `See "${c} in ${a}" on Google Maps`,
    filter: "Search by name…", hours: "Hours", whole: "Whole area",
    gmaps: "Google Maps", seeAllG: (c) => `All ${c} on Google Maps`, onMap: "See on map",
    catNone: "Not listed here in the map data. Check Google Maps:",
    catMore: (n) => `+ ${n} more (on the map)`, near: "near",
    nearestOf: (c) => `Nearest: ${c}`, nearestTitle: (a, f) => `Nearest help in ${a} (from ${f})`, sos: "Emergency: 1122",
    emptyRow: (a) => `Not in our map data for ${a}. Tap to see them on Google Maps:`,
    noData: (a) => `${a}: tap "All … on Google Maps" in any box to see the places there.`,
    widened: (a, r) => `Few places are listed in ${a}, so the nearest help within ${r} km is shown. Tap "All … on Google Maps" in any box for more.`
  };
  const catLabel = (k) => k === "all" ? T.all : (UR ? CATS[k].ur : CATS[k].en);

  // ---------------------------------------------------------
  // STATE
  // ---------------------------------------------------------
  const $ = (id) => document.getElementById(id);
  const statusEl = $("nearStatus"), listEl = $("nearList"), moreBtn = $("nearMore"), googleEl = $("nearGoogle");
  const input = $("nearInput"), sugEl = $("nearSuggest"), filterEl = $("nearFilter"), spin = $("nearSpin");

  let area = null;          // { label, s, w, n, e, lat, lon }
  let me = null;            // { lat, lon } from GPS
  let scope = "area";       // "area" or radius in metres
  let cat = "all";
  let places = new Map();   // id -> place
  const loaded = new Set(); // categories already fetched for this area/scope
  let shownLimit = 40;
  let map, cluster, meMarker, routeLayer;
  const markers = new Map();

  // never wait for a slow service for ever
  function fetchT(url, opts, ms) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), ms || 8000);
    const outer = opts && opts.signal;
    if (outer) outer.addEventListener("abort", () => ctrl.abort());
    return fetch(url, Object.assign({}, opts || {}, { signal: ctrl.signal })).finally(() => clearTimeout(timer));
  }
  function say(t, err) { statusEl.textContent = t || ""; statusEl.classList.toggle("err", !!err); }
  function busy(on) { spin.classList.toggle("hidden", !on); }
  function esc(t) { return String(t == null ? "" : t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function ic(n) { return '<span data-icon="' + n + '"></span>'; }
  function paint() { if (window.renderEmpathIcons) window.renderEmpathIcons(); }
  function distM(a, b) {
    const R = 6371000, r = (x) => x * Math.PI / 180;
    const h = Math.sin(r(b.lat - a.lat) / 2) ** 2 + Math.cos(r(a.lat)) * Math.cos(r(b.lat)) * Math.sin(r(b.lon - a.lon) / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }
  const fmtDist = (m) => m < 1000 ? Math.round(m) + " " + T.m : (m / 1000).toFixed(m < 10000 ? 1 : 0) + " " + T.km;
  const origin = () => me || (area ? { lat: area.lat, lon: area.lon } : null);

  // ---------------------------------------------------------
  // MAP
  // ---------------------------------------------------------
  function initMap() {
    if (map || !window.L) return;
    map = L.map("nearMap", { zoomControl: true, preferCanvas: true }).setView([30.3753, 69.3451], 5);
    // OpenStreetMap's own tiles: free, no API key (attribution required)
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);
    cluster = L.markerClusterGroup
      ? L.markerClusterGroup({ showCoverageOnHover: false, maxClusterRadius: 45, spiderfyOnMaxZoom: true, chunkedLoading: true })
      : L.layerGroup();
    cluster.addTo(map);
  }

  function pinIcon(k) {
    return L.divIcon({
      className: "help-pin-wrap",
      html: '<span class="help-pin" style="--c:' + CATS[k].color + '"><i>' + catSvg(k) + '</i></span>',
      iconSize: [30, 38], iconAnchor: [15, 36], popupAnchor: [0, -32]
    });
  }

  // ---------------------------------------------------------
  // 1) SUGGESTIONS WHILE TYPING (Photon)
  // ---------------------------------------------------------
  let sugTimer = null, sugItems = [], sugIndex = -1, sugCtrl = null;
  function parseQuery(q) {
    let found = null;
    for (const [k, re] of KEYWORDS) { if (re.test(q)) { found = k; break; } }
    let place = q;
    if (found) { for (const [k, re] of KEYWORDS) if (k === found) place = place.replace(new RegExp(re.source, "gi"), " "); }
    place = place.replace(FILLER, " ").replace(/[,.;:]+/g, " ").replace(/\s+/g, " ").trim();
    const nearMe = /\b(near me|nearby|mere paas|qareeb)\b|میرے قریب|میرے پاس/i.test(q) && !place;
    return { cat: found, place, nearMe };
  }

  function hideSug() { sugEl.classList.add("hidden"); sugEl.innerHTML = ""; sugItems = []; sugIndex = -1; }
  function showSug(items) {
    sugItems = items; sugIndex = -1;
    if (!items.length) return hideSug();
    sugEl.innerHTML = items.map((it, i) =>
      '<li role="option" data-i="' + i + '" class="sug-item">' + ic("pin") +
      '<div><b>' + esc(it.name) + '</b><span>' + esc(it.sub) + '</span></div></li>').join("");
    sugEl.classList.remove("hidden");
    paint();
  }
  input.addEventListener("input", function () {
    clearTimeout(sugTimer);
    const { place } = parseQuery(input.value);
    if (place.length < 2) return hideSug();
    sugTimer = setTimeout(async function () {
      try {
        if (sugCtrl) sugCtrl.abort();
        sugCtrl = new AbortController();
        const sig = sugCtrl.signal;
        let items = await ownPlaces(place, 6, sig);       // instant, from our own server
        if (sig.aborted) return;
        if (items && items.length) showSug(items);        // show at once; never wait for the backup
        if (items === null || items.length < 2) {         // few or none in our data: ask Photon too (backup)
          try {
            const r = await fetchT("https://photon.komoot.io/api/?limit=6&bbox=" + PK_BBOX + "&lang=en&q=" + encodeURIComponent(place), { signal: sig }, 5000);
            const d = await r.json();
            const more = (d.features || []).filter((f) => (f.properties.countrycode || "PK").toUpperCase() === "PK").map(function (f) {
              const p = f.properties, c = f.geometry.coordinates;
              const sub = [p.city && p.city !== p.name ? p.city : "", p.county, p.state].filter(Boolean).join(", ");
              let box = null;
              if (p.extent) box = { w: p.extent[0], n: p.extent[1], e: p.extent[2], s: p.extent[3] };
              return { name: p.name || place, sub: sub || "Pakistan", lat: c[1], lon: c[0], box };
            });
            items = (items || []).concat(more).slice(0, 6);
          } catch (e) { if (e.name === "AbortError" && sig.aborted) return; }
        }
        if (input.value.trim() && !sig.aborted) showSug(items || []);
      } catch (e) { if (e.name !== "AbortError") hideSug(); }
    }, 350);
  });
  input.addEventListener("keydown", function (e) {
    if (sugEl.classList.contains("hidden")) return;
    const lis = sugEl.querySelectorAll("li");
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      sugIndex = (sugIndex + (e.key === "ArrowDown" ? 1 : -1) + lis.length) % lis.length;
      lis.forEach((li, i) => li.classList.toggle("on", i === sugIndex));
    } else if (e.key === "Enter" && sugIndex >= 0) {
      e.preventDefault(); pickSug(sugIndex);
    } else if (e.key === "Escape") hideSug();
  });
  sugEl.addEventListener("mousedown", function (e) {
    const li = e.target.closest("li"); if (!li) return;
    e.preventDefault(); pickSug(+li.dataset.i);
  });
  input.addEventListener("blur", () => setTimeout(hideSug, 150));

  function pickSug(i) {
    const it = sugItems[i]; if (!it) return;
    const { cat: c } = parseQuery(input.value);
    stopSug();
    if (c) setCat(c, false);
    const box = it.box || boxAround(it.lat, it.lon, 6000);
    setArea({ label: it.name, lat: it.lat, lon: it.lon, ...box });
  }

  // ---------------------------------------------------------
  // 2) PLACE NAME -> AREA (Nominatim)
  // ---------------------------------------------------------
  function boxAround(lat, lon, m) {
    const dLat = m / 111320, dLon = m / (111320 * Math.cos(lat * Math.PI / 180));
    return { s: lat - dLat, n: lat + dLat, w: lon - dLon, e: lon + dLon };
  }
  // our own list of every city, town and village (null = not available on this server)
  let ownTowns = null;   // null = not asked yet
  async function ownPlaces(q, limit, signal) {
    if (ownTowns === false) return null;
    try {
      const r = await fetchT("/api/find_place?limit=" + (limit || 6) + "&lang=" + (UR ? "ur" : "en") + "&q=" + encodeURIComponent(q),
                             { credentials: "same-origin", signal }, 5000);
      if (!r.ok) { ownTowns = false; console.warn("[help map] /api/find_place not available (" + r.status + ")"); return null; }
      const d = await r.json();
      ownTowns = !!d.available;
      if (!d.available) { console.warn("[help map] data/help_towns.json not found: run tools/build_help_places.py again"); return null; }
      return d.results.map((t) => ({ name: t.name, sub: t.sub, lat: t.lat, lon: t.lon, box: boxAround(t.lat, t.lon, t.r) }));
    } catch (e) { return null; }
  }

  async function geocode(q) {
    const own = await ownPlaces(q, 1);
    if (own && own.length) {
      console.info("[help map] place found in our own data");
      return { label: own[0].name, lat: own[0].lat, lon: own[0].lon, ...own[0].box };
    }
    return geocodeOutside(q);
  }

  async function geocodeOutside(q) {
    // ask both free services at the same time; use Nominatim if it answers quickly, otherwise Photon
    const t0 = performance.now();
    const nom = geocodeNominatim(q).catch((e) => { console.warn("Nominatim:", e.message || e); return null; });
    const pho = geocodePhoton(q).catch((e) => { console.warn("Photon:", e.message || e); return null; });
    const quick = await Promise.race([nom, new Promise((r) => setTimeout(() => r("wait"), 2500))]);
    let g = quick !== "wait" ? quick : null;
    if (!g) g = await Promise.race([pho.then((x) => x || nom), nom.then((x) => x || pho)]);
    if (!g) g = (await nom) || (await pho);
    console.info("[help map] place found in " + Math.round(performance.now() - t0) + " ms");
    return g;
  }
  async function geocodePhoton(q) {
    const r = await fetchT("https://photon.komoot.io/api/?limit=5&bbox=" + PK_BBOX + "&lang=en&q=" + encodeURIComponent(q), {}, 8000);
    if (!r.ok) throw new Error("photon " + r.status);
    const d = await r.json();
    const f = (d.features || []).find((x) => (x.properties.countrycode || "PK").toUpperCase() === "PK");
    if (!f) return null;
    const p = f.properties, lat = f.geometry.coordinates[1], lon = f.geometry.coordinates[0];
    const box = p.extent ? { w: p.extent[0], n: p.extent[1], e: p.extent[2], s: p.extent[3] } : boxAround(lat, lon, 6000);
    return { label: p.name || q, lat, lon, ...box };
  }
  async function geocodeNominatim(q) {
    const url = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&countrycodes=pk&accept-language=" +
                (UR ? "ur,en" : "en") + "&q=" + encodeURIComponent(q);
    const r = await fetchT(url, { headers: { Accept: "application/json" } }, 8000);
    if (!r.ok) throw new Error("nominatim " + r.status);
    const d = await r.json();
    if (!d.length) return null;
    const b = d[0].boundingbox.map(Number); // [s, n, w, e]
    const lat = +d[0].lat, lon = +d[0].lon;
    let box = { s: b[0], n: b[1], w: b[2], e: b[3] };
    if ((box.n - box.s) < 0.01 && (box.e - box.w) < 0.01) box = boxAround(lat, lon, 6000); // a single point
    const label = (d[0].name || d[0].display_name.split(",")[0]).trim();
    return { label, lat, lon, ...box };
  }

  function stopSug() { clearTimeout(sugTimer); if (sugCtrl) sugCtrl.abort(); hideSug(); }
  $("nearForm").addEventListener("submit", async function (e) {
    e.preventDefault();
    stopSug();          // the search has started: no late suggestion list on top of the results
    const raw = input.value.trim();
    if (!raw) return say(T.typeFirst, true);
    const { cat: c, place, nearMe } = parseQuery(raw);
    if (c) setCat(c, false);
    if (nearMe || (!place && c)) return useGps();
    initMap(); busy(true); say(T.finding);
    try {
      const g = await geocode(place || raw);
      busy(false);
      if (!g) return say(T.notFound, true);
      setArea(g);
    } catch (err) { console.warn(err); busy(false); say(T.netErr, true); }
  });

  // which categories to load: the list shows every category, the map only the essential ones
  function wanted() {
    return cat !== "all" ? [cat] : CAT_KEYS.filter((k) => CATS[k].essential);
  }

  function setArea(a) {
    area = a; area.widened = 0; scope = "area"; markScope();
    if (view === "google") updateGoogleView();
    // very large areas (a province or a whole district): use 25 km around the centre instead
    if ((a.n - a.s) > 1.2 || (a.e - a.w) > 1.2) { scope = 25000; markScope(); area.tooBig = true; }
    resetData();
    drawArea(true);
    load(wanted());
  }

  // ---------------------------------------------------------
  // 3) GPS
  // ---------------------------------------------------------
  $("nearGpsBtn").addEventListener("click", useGps);
  function useGps() {
    initMap();
    if (!window.isSecureContext || !navigator.geolocation) return say(T.gpsInsecure, true);
    say(T.gpsAsk); busy(true);
    navigator.geolocation.getCurrentPosition(function (pos) {
      busy(false);
      me = { lat: pos.coords.latitude, lon: pos.coords.longitude };
      drawMe();
      if (!area || distM(me, area) > 30000) {
        area = { label: T.fromYou, lat: me.lat, lon: me.lon, ...boxAround(me.lat, me.lon, 10000) };
        scope = 10000; markScope(); resetData();
        drawArea(true);
        load(wanted());
      } else { render(); }
    }, function () { busy(false); say(T.gpsDenied, true); }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
  }
  function drawMe() {
    if (!map || !me) return;
    if (meMarker) map.removeLayer(meMarker);
    meMarker = L.circleMarker([me.lat, me.lon], { radius: 9, color: "#fff", weight: 3, fillColor: "#2563eb", fillOpacity: 1 })
      .addTo(map).bindPopup("<b>" + esc(T.you) + "</b>");
  }

  // ---------------------------------------------------------
  // PLACES: /api/help_places (Pakistan OpenStreetMap data saved on the Empath AI server)
  // ---------------------------------------------------------
  let localAvailable = null;   // null = not asked yet
  function searchBox() {
    if (scope === "area") return { s: area.s, w: area.w, n: area.n, e: area.e };
    const c = me && distM(me, area) < 30000 ? me : area;
    return boxAround(c.lat, c.lon, scope);
  }
  async function localPlaces(need) {
    if (localAvailable === false) return null;
    const b = searchBox();
    const url = "/api/help_places?s=" + b.s.toFixed(5) + "&w=" + b.w.toFixed(5) + "&n=" + b.n.toFixed(5) + "&e=" + b.e.toFixed(5) +
                "&cats=" + need.join(",");
    try {
      const t0 = performance.now();
      const r = await fetchT(url, { credentials: "same-origin" }, 15000);
      if (!r.ok) { if (r.status === 404) localAvailable = false; console.warn("[help map] /api/help_places: " + r.status); return null; }
      const d = await r.json();
      localAvailable = !!d.available;
      console.info("[help map] own data: " + (d.places || []).length + " places in " + Math.round(performance.now() - t0) + " ms");
      if (!d.available) console.warn("[help map] data/help_places.json not found on the server");
      return d.available ? d.places : null;
    } catch (e) { return null; }
  }
  function addLocal(list) {
    list.forEach(function (p, i) {
      // [lat, lon, cat, name, name_ur, phone]  (older data: only the first 4)
      const lat = p[0], lon = p[1], c = p[2], nm = (UR && p[4]) || p[3] || p[4] || "";
      if (!CATS[c]) return;
      const id = "l" + lat + "," + lon + c;
      if (places.has(id)) return;
      places.set(id, { id, lat, lon, cat: c, name: nm || catLabel(c), named: !!nm, gname: p[3] || nm,
                       phone: p[5] || "", web: "", hours: "", addr: "" });
    });
  }

  let loadToken = 0;
  async function load(keys) {
    const need = keys.filter((k) => !loaded.has(k));
    if (!need.length) return render();
    const token = ++loadToken;
    busy(true); say(T.searching(area.label));
    renderList(true);                       // the category boxes appear at once
    const local = await localPlaces(need);
    if (token !== loadToken) return;
    busy(false);
    need.forEach((k) => loaded.add(k));
    if (!local) {
      // no saved data on this server: every box still has its "see all on Google Maps" link
      render();
      return say(T.noData(area.label));
    }
    addLocal(local);
    afterLoad(need);
  }

  function afterLoad(need) {
    // Small towns (e.g. interior Sindh) often have few places in the map data:
    // look wider automatically, so the user always sees the NEAREST help, even in the next town.
    // "All": look wider when fewer than 5 places; one category: only when there is none at all
    if (visible().length < (cat === "all" ? 5 : 1) && !area.tooBig && !area.userScope) {
      const next = scope === "area" || scope < 50000 ? 50000 : (scope < 100000 ? 100000 : null);
      if (next) {
        scope = next; area.widened = next / 1000; markScope(); resetData(); drawArea(true);
        return load(wanted());
      }
    }
    shownLimit = 40; render();
  }

  // ---------------------------------------------------------
  // 5) SHOW ON MAP + LIST
  // ---------------------------------------------------------
  function visible() {
    const o = origin();
    const f = (filterEl.value || "").trim().toLowerCase();
    const centre = me && distM(me, area) < 30000 ? me : area;
    const inScope = (p) => scope === "area" || distM(centre, p) <= scope;
    return [...places.values()]
      .filter((p) => (cat === "all" ? CATS[p.cat].essential : p.cat === cat))
      .filter(inScope)
      .filter((p) => !f || (p.name + " " + p.addr).toLowerCase().includes(f))
      .map((p) => Object.assign(p, { d: o ? distM(o, p) : 0 }))
      .sort((a, b) => (b.named - a.named) || (a.d - b.d));
  }

  // ---------------------------------------------------------
  // GOOGLE MAPS RESULTS (tab): Google's own search inside the page, no API key
  // ---------------------------------------------------------
  let view = "list";
  const gFrame = $("googleFrame"), gView = $("googleView"), oView = $("osmView"), lView = $("listView");
  const gridEl = $("catGrid"), emptyEl = $("catEmpty"), nearestEl = $("catNearest");
  function googleQuery() {
    if (!area) return "";
    const what = cat === "all" ? "hospital NGO NADRA" : CATS[cat].g;
    return what + " near " + area.label + ", Pakistan";
  }
  function updateGoogleView() {
    if (view !== "google" || !area) return;
    const src = "https://maps.google.com/maps?output=embed&hl=" + (UR ? "ur" : "en") + "&z=13&q=" + encodeURIComponent(googleQuery());
    if (gFrame.getAttribute("src") !== src) gFrame.setAttribute("src", src);
  }
  function switchView(v) {
    view = v;
    document.querySelectorAll("#nearTabs .tab").forEach((x) => x.classList.toggle("on", x.dataset.view === v));
    gView.classList.toggle("hidden", view !== "google");
    oView.classList.toggle("hidden", view !== "osm");
    lView.classList.toggle("hidden", view !== "list");
    if (view === "google") {
      if (!area) return say(T.typeFirst, true);
      updateGoogleView();
    } else if (view === "osm" && map) {
      // the map was hidden: let it measure itself again, then show the searched area
      setTimeout(function () { map.invalidateSize(); if (area) drawArea(true); render(); }, 60);
    } else if (view === "list" && area) {
      load(wanted());
    }
  }
  document.querySelectorAll("#nearTabs .tab").forEach(function (b) {
    b.addEventListener("click", () => switchView(b.dataset.view));
  });

  function showGoogle() {
    if (!area) return;
    const gq = (cat === "all" ? CATS.ngo.g : CATS[cat].g) + " in " + area.label;
    googleEl.href = "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(gq);
    googleEl.querySelector("span:last-child").textContent = T.google(cat === "all" ? catLabel("ngo") : catLabel(cat), area.label);
    googleEl.classList.remove("hidden");
  }

  // the searched place itself (big dark pin) + the chosen distance as a circle
  let areaPin = null, scopeCircle = null;
  function drawArea(fit) {
    if (!map || !area) return;
    if (areaPin) map.removeLayer(areaPin);
    if (area.label !== T.fromYou) {
      areaPin = L.marker([area.lat, area.lon], {
        icon: L.divIcon({ className: "help-pin-wrap", html: '<span class="area-pin"></span>', iconSize: [34, 44], iconAnchor: [17, 42], popupAnchor: [0, -38] }),
        zIndexOffset: 1000, title: area.label
      }).addTo(map).bindPopup("<b>" + esc(area.label) + "</b>");
    }
    if (scopeCircle) { map.removeLayer(scopeCircle); scopeCircle = null; }
    if (scope !== "area") {
      const c = me && distM(me, area) < 30000 ? me : area;
      scopeCircle = L.circle([c.lat, c.lon], { radius: scope, color: "#0f766e", weight: 2, dashArray: "6 6", fillColor: "#14b8a6", fillOpacity: 0.06 }).addTo(map);
      if (fit) map.fitBounds(scopeCircle.getBounds(), { padding: [20, 20] });
    } else if (fit) {
      map.fitBounds([[area.s, area.w], [area.n, area.e]], { padding: [20, 20] });
    }
  }

  // ---------------------------------------------------------
  // CATEGORY LIST: every category in its own box, each place with a Google Maps link
  // ---------------------------------------------------------
  const PER_CAT = 6;
  function placeLink(p) {
    // Google Maps link, no API key needed.
    // Named place: Google's own listing (phone, timings, photos). No name: the exact spot.
    if (p.named && area && area.label !== T.fromYou && !(p.d > 15000)) {   // far places may be in another town: use the exact spot
      return "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent((p.gname || p.name) + ", " + area.label);
    }
    return "https://www.google.com/maps/search/?api=1&query=" + p.lat.toFixed(6) + "," + p.lon.toFixed(6);
  }
  function catSearchLink(k) {
    const where = area.label === T.fromYou ? area.lat.toFixed(4) + "," + area.lon.toFixed(4) : area.label + ", Pakistan";
    return "https://www.google.com/maps/search/?api=1&query=" + encodeURIComponent(CATS[k].g + " near " + where);
  }
  function renderList(stillLoading) {
    if (!area) return;
    emptyEl.classList.add("hidden");
    const o = origin();
    const centre = me && distM(me, area) < 30000 ? me : area;
    const groups = {};
    places.forEach(function (p) {
      if (scope !== "area" && distM(centre, p) > scope) return;
      (groups[p.cat] = groups[p.cat] || []).push(p);
    });
    const keys = wanted();
    // categories with places get a box; while loading every box shows; the empty ones go in one short row
    const boxKeys = keys.filter((k) => groups[k] || (stillLoading && !loaded.has(k)) || keys.length === 1);
    const emptyKeys = keys.filter((k) => !boxKeys.includes(k));
    // the single nearest place of the most needed kinds, as big tiles
    const NEAREST = ["health", "pharmacy", "rescue", "police", "nadra", "bank", "ngo", "govt"];
    const tiles = (cat === "all" ? NEAREST : []).map(function (k) {
      const g = groups[k]; if (!g || !g.length) return "";
      const p = g.reduce((a, b) => (distM(o || area, a) <= distM(o || area, b) ? a : b));
      p.d = o ? distM(o, p) : 0;
      return '<div class="near-tile"><span class="cat-ic big" style="background:' + CATS[k].color + '">' + catSvg(k) + '</span>' +
        '<div class="nt-body"><div class="nt-k">' + esc(T.nearestOf(catLabel(k))) + '</div>' +
        '<div class="nt-name">' + esc(p.name) + '</div>' +
        '<div class="nt-d" dir="ltr">' + (p.d ? fmtDist(p.d) : '') + '</div>' +
        '<div class="nt-acts"><a class="gm" target="_blank" rel="noopener" href="' + navUrl(p) + '">' + esc(T.nav) + '</a>' +
        (p.phone ? '<a class="dir" href="' + telHref(p.phone) + '">' + esc(T.call) + '</a>' : '') +
        '<a class="dir" target="_blank" rel="noopener" href="' + placeLink(p) + '">' + esc(T.gmaps) + '</a></div></div></div>';
    }).filter(Boolean);
    nearestEl.innerHTML = tiles.length ? '<div class="near-head">' + esc(T.nearestTitle(area.label, me ? T.fromYou : T.fromCentre)) +
        '<a class="near-sos" href="tel:1122">' + esc(T.sos) + '</a></div><div class="near-tiles">' + tiles.join("") + '</div>' : "";

    gridEl.innerHTML = boxKeys.map(function (k) {
      const list = (groups[k] || []).map((p) => Object.assign(p, { d: o ? distM(o, p) : 0 }))
        .sort((a, b) => (b.named - a.named) || (a.d - b.d)).slice(0, PER_CAT * 4)
        .sort((a, b) => a.d - b.d);
      const total = (groups[k] || []).length;
      const pending = stillLoading && !loaded.has(k);
      const rows = list.slice(0, PER_CAT).map(function (p) {
        return '<div class="cat-row"><div class="nm">' + esc(p.name) +
          '<small>' + (p.d ? '<span dir="ltr">' + fmtDist(p.d) + '</span>' : '') + '</small></div>' +
          (p.phone ? '<a class="dir" href="' + telHref(p.phone) + '">' + esc(T.call) + '</a>' : '') +
          '<a class="gm" target="_blank" rel="noopener" href="' + placeLink(p) + '">' + esc(T.gmaps) + '</a>' +
          '<a class="dir" target="_blank" rel="noopener" href="' + navUrl(p) + '">' + esc(T.nav) + '</a></div>';
      }).join("");
      return '<div class="cat-card"><div class="cat-head"><span class="cat-ic" style="background:' + CATS[k].color + '">' + catSvg(k) + '</span>' +
        '<h3>' + esc(catLabel(k)) + '</h3><span class="n" dir="ltr">' + (pending ? "…" : total) + '</span></div>' +
        (rows || (pending ? '' : '<div class="cat-none">' + esc(T.catNone) + '</div>')) +
        '<div class="cat-foot"><a target="_blank" rel="noopener" href="' + catSearchLink(k) + '">' + esc(T.seeAllG(catLabel(k))) + '</a>' +
        (total ? '<button type="button" data-showcat="' + k + '">' + esc(total > PER_CAT ? T.catMore(total - PER_CAT) : T.onMap) + '</button>' : '') +
        '</div></div>';
    }).join("") + (emptyKeys.length ? '<div class="cat-card cat-empty"><div class="cat-none">' + esc(T.emptyRow(area.label)) + '</div>' +
        '<div class="cat-links">' + emptyKeys.map((k) => '<a target="_blank" rel="noopener" href="' + catSearchLink(k) + '">' +
        '<span class="dot" style="background:' + CATS[k].color + '"></span>' + esc(catLabel(k)) + '</a>').join("") + '</div></div>' : "");
  }
  gridEl.addEventListener("click", function (e) {
    const b = e.target.closest("[data-showcat]"); if (!b) return;
    setCat(b.dataset.showcat, true);
    switchView("osm");
  });

  function render(stillLoading) {
    renderList(stillLoading);
    if (!map || !area) return;
    if (view !== "osm") {
      // map is hidden: only update the message; pins are drawn when the map tab is opened
      updateCounts();
      const n = visible().length;
      if (n) say((area.tooBig ? T.tooBig + " " : "") + (area.widened ? T.widened(area.label, area.widened) + " " : "") +
                 T.found(n, area.label, me ? T.fromYou : T.fromCentre) + (stillLoading ? " …" : ""));
      else if (!stillLoading) say(T.noneAll(area.label));
      return;
    }
    cluster.clearLayers(); markers.clear();
    if (routeLayer) { map.removeLayer(routeLayer); routeLayer = null; }
    drawMe();
    updateCounts();

    const all = visible();
    const sortedByDist = all.slice().sort((a, b) => a.d - b.d);
    showGoogle(); updateGoogleView();

    if (!all.length) {
      listEl.innerHTML = ""; moreBtn.classList.add("hidden");
      if (!stillLoading) say(cat === "all" ? T.noneAll(area.label) : T.none(catLabel(cat), area.label));
      return;
    }
    say((area.tooBig ? T.tooBig + " " : "") + (area.widened ? T.widened(area.label, area.widened) + " " : "") +
        T.found(all.length, area.label, me ? T.fromYou : T.fromCentre) + (stillLoading ? " …" : ""));

    const ms = all.map(function (p) {
      const mk = L.marker([p.lat, p.lon], { icon: pinIcon(p.cat), title: p.name });
      mk.bindPopup(popupHtml(p), { maxWidth: 260 });
      mk.on("click", () => highlight(p.id, false));
      markers.set(p.id, mk);
      return mk;
    });
    if (cluster.addLayers) cluster.addLayers(ms); else ms.forEach((m) => cluster.addLayer(m));

    listEl.innerHTML = "";
    sortedByDist.slice(0, shownLimit).forEach((p) => listEl.appendChild(cardEl(p)));
    const rest = sortedByDist.length - shownLimit;
    moreBtn.classList.toggle("hidden", rest <= 0);
    if (rest > 0) moreBtn.textContent = T.more(Math.min(rest, 40));
    paint();
  }

  function navUrl(p) {
    // Google uses the phone's own location as the start: real turn-by-turn navigation
    return "https://www.google.com/maps/dir/?api=1&destination=" + p.lat + "," + p.lon + (me ? "&origin=" + me.lat + "," + me.lon : "");
  }
  function telHref(ph) { return "tel:" + ph.replace(/[^\d+]/g, ""); }

  function popupHtml(p) {
    return '<div class="pop"><b>' + esc(p.name) + '</b><div class="pop-cat"><i style="background:' + CATS[p.cat].color + '"></i>' +
      esc(catLabel(p.cat)) + (p.d ? ' · <span dir="ltr">' + fmtDist(p.d) + '</span>' : '') + '</div>' +
      (p.addr ? '<div class="pop-line">' + esc(p.addr) + '</div>' : '') +
      (p.hours ? '<div class="pop-line">' + esc(T.hours) + ': <span dir="ltr">' + esc(p.hours) + '</span></div>' : '') +
      '<div class="pop-acts"><a target="_blank" rel="noopener" href="' + navUrl(p) + '">' + esc(T.nav) + '</a>' +
      '<a target="_blank" rel="noopener" href="' + placeLink(p) + '">' + esc(T.gmaps) + '</a>' +
      (p.phone ? '<a href="' + telHref(p.phone) + '">' + esc(T.call) + '</a>' : '') + '</div></div>';
  }

  function cardEl(p) {
    const el = document.createElement("div");
    el.className = "place"; el.id = "pl-" + p.id;
    el.innerHTML =
      '<div class="flex items-start justify-between gap-2"><div class="place-name">' + esc(p.name) + '</div>' +
      (p.d ? '<span class="place-dist" dir="ltr">' + fmtDist(p.d) + '</span>' : '') + '</div>' +
      '<div class="place-meta"><span class="inline-flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-full" style="background:' +
      CATS[p.cat].color + '"></span>' + esc(catLabel(p.cat)) + '</span>' + (p.addr ? '<span>' + esc(p.addr) + '</span>' : '') + '</div>' +
      '<div class="place-acts">' +
        '<a class="pbtn" target="_blank" rel="noopener" href="' + navUrl(p) + '">' + ic("navigate") + esc(T.nav) + '</a>' +
        '<button type="button" class="pbtn soft" data-act="route">' + ic("route") + esc(T.route) + '</button>' +
        (p.phone ? '<a class="pbtn soft" href="' + telHref(p.phone) + '">' + ic("phone") + esc(T.call) + '</a>' : '') +
        (p.web ? '<a class="pbtn soft" target="_blank" rel="noopener" href="' + esc(p.web.startsWith("http") ? p.web : "https://" + p.web) + '">' + esc(T.web) + '</a>' : '') +
        '<a class="pbtn soft" target="_blank" rel="noopener" href="' + placeLink(p) + '">' + esc(T.gmaps) + '</a>' +
      '</div>';
    el.addEventListener("click", function (e) {
      if (e.target.closest("a")) return;
      highlight(p.id, true);
      if (e.target.closest('[data-act="route"]')) drawRoute(p);
    });
    return el;
  }

  function highlight(id, fromList) {
    listEl.querySelectorAll(".place").forEach((el) => el.classList.toggle("sel", el.id === "pl-" + id));
    const mk = markers.get(id);
    if (mk && fromList) {
      if (cluster.zoomToShowLayer) cluster.zoomToShowLayer(mk, () => mk.openPopup()); else { map.panTo(mk.getLatLng()); mk.openPopup(); }
    }
    if (!fromList) { const el = $("pl-" + id); if (el) el.scrollIntoView({ block: "nearest", behavior: "smooth" }); }
  }

  async function drawRoute(p) {
    if (!me) return say(T.routeNeedsGps, true);
    say(T.routing);
    try {
      const r = await fetch("https://router.project-osrm.org/route/v1/driving/" + me.lon + "," + me.lat + ";" + p.lon + "," + p.lat +
                            "?overview=full&geometries=geojson");
      const d = await r.json();
      if (!d.routes || !d.routes.length) throw new Error("no route");
      if (routeLayer) map.removeLayer(routeLayer);
      routeLayer = L.geoJSON(d.routes[0].geometry, { style: { color: "#0f766e", weight: 6, opacity: 0.85 } }).addTo(map);
      map.fitBounds(routeLayer.getBounds(), { padding: [40, 40] });
      say(T.routeInfo(p.name, fmtDist(d.routes[0].distance), Math.max(1, Math.round(d.routes[0].duration / 60))));
    } catch (e) { console.warn(e); say(T.routeErr, true); }
  }

  // ---------------------------------------------------------
  // CONTROLS: categories, scope, filter, more
  // ---------------------------------------------------------
  const catsEl = $("nearCats");
  ["all"].concat(CAT_KEYS).forEach(function (k) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip" + (k === "all" ? " on" : ""); b.dataset.cat = k;
    b.innerHTML = (k === "all" ? "" : '<span class="chip-ic" style="background:' + CATS[k].color + '">' + catSvg(k) + '</span>') +
                  '<span>' + esc(catLabel(k)) + '</span><span class="cnt" dir="ltr"></span>';
    b.addEventListener("click", () => setCat(k, true));
    catsEl.appendChild(b);
  });
  function setCat(k, fetchNow) {
    cat = k; shownLimit = 40;
    catsEl.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c.dataset.cat === k));
    updateGoogleView();
    if (!fetchNow || !area) return;
    load(wanted());
  }
  function updateCounts() {
    const counts = {};
    let total = 0;
    const centre = area && me && distM(me, area) < 30000 ? me : area;
    places.forEach(function (p) {
      if (scope !== "area" && centre && distM(centre, p) > scope) return;   // count only what is inside the chosen distance
      counts[p.cat] = (counts[p.cat] || 0) + 1; if (CATS[p.cat].essential) total++;
    });
    catsEl.querySelectorAll(".chip").forEach(function (c) {
      const k = c.dataset.cat, n = k === "all" ? total : (counts[k] || 0);
      c.querySelector(".cnt").textContent = (k === "all" || loaded.has(k)) && area ? " " + n : "";
    });
  }

  const scopeEl = $("nearScope");
  const wholeBtn = scopeEl.querySelector('[data-r="area"]');
  if (wholeBtn) wholeBtn.textContent = T.whole;
  function markScope() { scopeEl.querySelectorAll("[data-r]").forEach((b) => b.classList.toggle("on", String(scope) === b.dataset.r)); }
  scopeEl.querySelectorAll("[data-r]").forEach(function (b) {
    b.addEventListener("click", function () {
      if (!area) return;
      scope = b.dataset.r === "area" ? "area" : +b.dataset.r;
      area.tooBig = false; area.widened = 0; area.userScope = true;   // keep exactly what the user chose
      markScope(); resetData();
      drawArea(true);
      load(wanted());
    });
  });
  function resetData() { places = new Map(); loaded.clear(); listEl.innerHTML = ""; filterEl.value = ""; }

  filterEl.setAttribute("placeholder", T.filter);
  filterEl.addEventListener("input", () => { shownLimit = 40; render(); });
  moreBtn.addEventListener("click", () => { shownLimit += 40; render(); });

  // example searches
  document.querySelectorAll("[data-example]").forEach(function (b) {
    b.addEventListener("click", function () { input.value = b.dataset.example; $("nearForm").requestSubmit(); });
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initMap); else initMap();
})();
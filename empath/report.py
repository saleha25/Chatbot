"""Professional wellbeing report (printable / downloadable as PDF).

A separate page at /report. It only READS existing data:
accounts, users, user_memory, wellbeing_logs, assessments, chat_history.
Nothing in the chat, wellness or assessment code is changed.

One gpt-4o-mini call writes the professional narrative. It is cached, so
reloading or printing the same report does not call the API again.
If the API is unavailable, a rule-based summary is used instead.
"""
import json
import os
import re
import secrets
from collections import Counter
from datetime import datetime, timedelta, timezone

from flask import render_template, request, session

from .core import app, login_required

PK_TZ = timezone(timedelta(hours=5))          # Pakistan Standard Time (no daylight saving)
REPORT_PERIODS = {"7": 7, "30": 30, "90": 90, "all": 0}
_AI_CACHE = {}                                 # (user, period, data fingerprint) -> narrative

ROLE_LABELS = {
    "student": "Student",
    "homemaker": "Homemaker",
    "parent": "Parent / caregiver",
    "unemployed": "Currently unemployed",
    "daily_worker": "Daily-wage worker",
    "elderly": "Elderly / retired",
    "government": "Employed (government / private)",
    "default": "Not specified",
}
DISPLACEMENT_LABELS = {
    "flood": "Floods",
    "conflict": "Conflict / insecurity",
    "earthquake": "Earthquake",
    "eviction": "Eviction / loss of housing",
    "fire": "Fire",
    "displacement": "Displacement (cause not specified)",
}
MOOD_SCORE = {"very_low": 1, "very low": 1, "low": 2, "anxious": 2, "okay": 3, "good": 4, "better": 4}
MOOD_LABEL = {"very_low": "Very low", "low": "Low", "anxious": "Anxious", "okay": "Okay", "good": "Good", "better": "Good"}
ENERGY_SCORE = {"very_low": 1, "very low": 1, "low": 2, "medium": 3, "high": 4}
ENERGY_LABEL = {"very_low": "Very low", "low": "Low", "medium": "Medium", "high": "High"}

# Extra account columns that should never appear on a report
HIDDEN_ACCOUNT_COLUMNS = {"id", "password_hash", "full_name", "email", "dob", "created_at", "urdu_name"}

# Signs of suicidal thinking / self-harm in user messages (English, Roman Urdu, Urdu)
RISK_PATTERNS = [
    r"\bkill (my ?self|me)\b", r"\bsuicid", r"\bend (my|this) life\b", r"\bwant to die\b",
    r"\bdon'?t want to live\b", r"\bno reason to (live|keep going)\b", r"\bhurt (my ?self)\b",
    r"\bself[- ]?harm\b", r"\bbetter off (dead|without me)\b", r"\bcut (my ?self)\b",
    r"\bkhud ?kushi\b", r"\bkhudkushi\b", r"\bmar(na)? jana?\b", r"\bjeena nahi\b", r"\bzinda nahi rehna\b",
    r"\bmarna chahta\b", r"\bmarna chahti\b", r"\bkhatam kar (dun|doon|lun|loon)\b",
    r"خودکشی", r"خود کشی", r"مرنا چاہت", r"مر جاؤں", r"مر جانا", r"جینا نہیں", r"زندہ نہیں رہنا",
    r"زندگی ختم", r"اپنے آپ کو نقصان",
]
_RISK_RE = re.compile("|".join(RISK_PATTERNS), re.IGNORECASE)

# Emotional themes looked for in the user's own messages
EMOTION_THEMES = {
    "Sadness / low mood": ["sad", "depressed", "cry", "crying", "hopeless", "udaas", "udas", "dukh", "rona",
                           "اداس", "مایوس", "رونا", "دکھ"],
    "Anxiety / worry": ["anxious", "worried", "worry", "panic", "nervous", "fear", "scared", "tension", "pareshan",
                        "ghabrahat", "dar ", "فکر", "پریشان", "گھبراہٹ", "ڈر", "خوف"],
    "Sleep difficulties": ["sleep", "insomnia", "nightmare", "awake at night", "neend", "khwab", "نیند", "خواب"],
    "Grief and loss": ["miss", "lost", "died", "passed away", "death", "grave", "yaad", "qabar", "یاد", "فوت", "قبر"],
    "Loneliness / isolation": ["alone", "lonely", "nobody", "no one", "ignored", "akela", "akeli", "tanha",
                               "اکیلا", "اکیلی", "تنہا"],
    "Anger / frustration": ["angry", "anger", "frustrated", "irritated", "gussa", "ghussa", "غصہ", "چڑچڑا"],
    "Fatigue / low energy": ["tired", "exhausted", "drained", "weak", "thaka", "thaki", "thakan", "تھکا", "تھکی", "تھکن",
                             "کمزوری"],
}
HARDSHIP_LABELS = {
    "loss_of_home": "Loss of home",
    "camp_stress": "Camp / temporary shelter conditions",
    "family_burden": "Family responsibilities",
    "livelihood_loss": "Loss of income / work",
    "education_disruption": "Disrupted education",
    "safety_fear": "Safety concerns",
    "grief_memory": "Longing for former life",
}


# ─────────────────────────────────────────────
# URDU VERSION OF THE REPORT
# Labels that come from the data (bands, roles, themes...) are written in
# English inside the code and translated here when the report is in Urdu.
# ─────────────────────────────────────────────
URDU_MONTHS = ["جنوری", "فروری", "مارچ", "اپریل", "مئی", "جون",
               "جولائی", "اگست", "ستمبر", "اکتوبر", "نومبر", "دسمبر"]

UR = {
    # roles / professions
    "Student": "طالب علم", "Homemaker": "گھریلو خاتون", "Parent / caregiver": "والدین / نگہداشت کرنے والے",
    "Currently unemployed": "فی الحال بے روزگار", "Daily-wage worker": "دیہاڑی دار مزدور",
    "Elderly / retired": "بزرگ / ریٹائرڈ", "Employed (government / private)": "ملازم (سرکاری / نجی)",
    "Not specified": "درج نہیں", "Not stated": "نہیں بتایا گیا", "Yes": "جی ہاں",
    # displacement
    "Floods": "سیلاب", "Conflict / insecurity": "تنازع / بدامنی", "Earthquake": "زلزلہ",
    "Eviction / loss of housing": "بے دخلی / گھر کا چھن جانا", "Fire": "آگ",
    "Displacement (cause not specified)": "نقل مکانی (وجہ درج نہیں)",
    # hardships
    "Loss of home": "گھر کا چھن جانا", "Camp / temporary shelter conditions": "کیمپ / عارضی رہائش کے حالات",
    "Family responsibilities": "خاندانی ذمہ داریاں", "Loss of income / work": "آمدنی / کام کا چھن جانا",
    "Disrupted education": "تعلیم میں رکاوٹ", "Safety concerns": "تحفظ کے خدشات",
    "Longing for former life": "پرانی زندگی کی یاد",
    # emotional themes
    "Sadness / low mood": "اداسی / کم موڈ", "Anxiety / worry": "بے چینی / فکر",
    "Sleep difficulties": "نیند کے مسائل", "Grief and loss": "غم اور نقصان",
    "Loneliness / isolation": "تنہائی", "Anger / frustration": "غصہ / جھنجھلاہٹ",
    "Fatigue / low energy": "تھکن / کم توانائی",
    # bands
    "Minimal": "بہت کم", "Mild": "ہلکا", "Moderate": "درمیانہ", "Moderately severe": "کافی شدید",
    "Severe": "شدید", "Very low wellbeing": "بہت کم فلاح", "Low wellbeing": "کم فلاح",
    "Moderate wellbeing": "درمیانی فلاح", "Good wellbeing": "اچھی فلاح",
    "Mild Depression": "ہلکا ڈپریشن", "Moderate Depression": "درمیانہ ڈپریشن", "Severe Depression": "شدید ڈپریشن",
    "Minimal Depression": "بہت کم ڈپریشن",
    # mood / energy
    "Very low": "بہت کم", "Low": "کم", "Anxious": "بے چین", "Okay": "ٹھیک", "Good": "اچھا",
    "Medium": "درمیانہ", "High": "زیادہ",
    # chat stats
    "Morning (5am–12pm)": "صبح (۵ سے ۱۲ بجے)", "Afternoon (12–5pm)": "دوپہر (۱۲ سے ۵ بجے)",
    "Evening (5–10pm)": "شام (۵ سے ۱۰ بجے)", "Late night (10pm–5am)": "رات گئے (۱۰ سے ۵ بجے)",
    "Mostly Urdu": "زیادہ تر اردو", "Mostly English / Roman Urdu": "زیادہ تر انگریزی / رومن اردو",
    "Urdu and English": "اردو اور انگریزی",
    "No change": "کوئی تبدیلی نہیں",
}


def tx(text, lang):
    """Translate a known label to Urdu when the report is in Urdu."""
    if lang != "ur" or text is None:
        return text
    return UR.get(text, text)


# ─────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────
def _row_get(row, key, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _parse_dt(value):
    """SQLite text -> datetime (naive). Returns None if it cannot be read."""
    if not value:
        return None
    text = str(value).strip().replace("T", " ")[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:len(fmt) + 2], fmt)
        except ValueError:
            continue
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _utc_to_pk(dt):
    return dt + timedelta(hours=5) if dt else None


def _fmt_date(dt, lang="en"):
    if not dt:
        return "—"
    if lang == "ur":
        return f"{dt.day} {URDU_MONTHS[dt.month - 1]} {dt.year}"
    return dt.strftime("%d %b %Y")


def _fmt_short(dt, lang="en"):
    if not dt:
        return ""
    if lang == "ur":
        return f"{dt.day} {URDU_MONTHS[dt.month - 1]}"
    return dt.strftime("%d %b")


def _fmt_datetime(dt, lang="en"):
    if not dt:
        return "—"
    if lang == "ur":
        ampm = "صبح" if dt.hour < 12 else "شام"
        return f"{_fmt_date(dt, lang)}، {dt.strftime('%I:%M')} {ampm}"
    return dt.strftime("%d %b %Y, %I:%M %p")


def _age_from_dob(dob_text):
    dob = _parse_dt(dob_text)
    if not dob:
        return None
    today = datetime.now(PK_TZ).date()
    years = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return years if 0 < years < 120 else None


def _phq_band(score):
    if score is None:
        return "—"
    if score <= 4:
        return "Minimal"
    if score <= 9:
        return "Mild"
    if score <= 14:
        return "Moderate"
    if score <= 19:
        return "Moderately severe"
    return "Severe"


def _gad_band(score):
    if score is None:
        return "—"
    if score <= 4:
        return "Minimal"
    if score <= 9:
        return "Mild"
    if score <= 14:
        return "Moderate"
    return "Severe"


def _who_band(score):
    """WHO-5 raw score 0-25 (higher is better)."""
    if score is None:
        return "—"
    if score <= 7:
        return "Very low wellbeing"
    if score <= 12:
        return "Low wellbeing"
    if score <= 18:
        return "Moderate wellbeing"
    return "Good wellbeing"


def _band_level(band):
    """Colour level for the badges: 1 good … 4 serious."""
    band = (band or "").lower()
    if band in ("minimal", "good wellbeing"):
        return 1
    if band in ("mild", "moderate wellbeing"):
        return 2
    if band in ("moderate", "low wellbeing"):
        return 3
    if band in ("moderately severe", "severe", "very low wellbeing"):
        return 4
    return 0


def _change(curr, prev, higher_is_better=False, lang="en"):
    if curr is None or prev is None:
        return {"text": "—", "dir": "none"}
    diff = curr - prev
    if diff == 0:
        return {"text": tx("No change", lang), "dir": "same"}
    better = (diff > 0) if higher_is_better else (diff < 0)
    sign = "+" if diff > 0 else "−"
    return {"text": f"{sign}{abs(diff)}", "dir": "better" if better else "worse"}


def _is_urdu(text):
    return bool(re.search(r"[؀-ۿ]", text or ""))


def _count_hits(text, words):
    t = f" {text.lower()} "
    return any(w in t for w in words)


# ─────────────────────────────────────────────
# Data collection
# ─────────────────────────────────────────────
def _collect(account_id, days, lang="en"):
    db = get_db()
    now_pk = datetime.now(PK_TZ).replace(tzinfo=None)
    since_pk = (now_pk - timedelta(days=days)) if days else None
    since_utc_text = (since_pk - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M:%S") if since_pk else None

    account = db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    user = get_user_by_account_id(account_id)
    user_id = _row_get(user, "id") or session.get("user_id")
    memory = db.execute("SELECT * FROM user_memory WHERE user_id = ?", (user_id,)).fetchone() if user_id else None

    # ---------- profile ----------
    role = _row_get(user, "role", "default") or "default"
    profession = None
    for col in ("profession", "occupation", "job"):
        if _row_get(account, col):
            profession = str(_row_get(account, col)).strip()
            break
    if not profession:
        profession = ROLE_LABELS.get(role, role.replace("_", " ").title())
    profession = tx(profession, lang)

    living_place = _row_get(user, "living_place", "") or _row_get(memory, "living_place", "")
    extra_fields = []
    if account is not None:
        for col in account.keys():
            if col in HIDDEN_ACCOUNT_COLUMNS or col in ("profession", "occupation", "job"):
                continue
            value = _row_get(account, col)
            if value not in (None, ""):
                extra_fields.append((col.replace("_", " ").title(), str(value)))

    # ---------- assessments ----------
    assess_sql = "SELECT * FROM assessments WHERE user_id = ?"
    params = [user_id]
    if since_utc_text:
        assess_sql += " AND assessment_date >= ?"
        params.append(since_utc_text)
    assess_rows = db.execute(assess_sql + " ORDER BY assessment_date ASC, id ASC", params).fetchall() if user_id else []
    all_assess = db.execute(
        "SELECT * FROM assessments WHERE user_id = ? ORDER BY assessment_date ASC, id ASC", (user_id,)
    ).fetchall() if user_id else []

    assessments = []
    for i, r in enumerate(assess_rows):
        phq, gad, who = _row_get(r, "phq9_total"), _row_get(r, "gad7_total"), _row_get(r, "who5_total")
        prev_phq, prev_gad, prev_who = _row_get(r, "prev_phq9"), _row_get(r, "prev_gad7"), _row_get(r, "prev_who5")
        if prev_phq is None and i > 0:
            p = assess_rows[i - 1]
            prev_phq, prev_gad, prev_who = _row_get(p, "phq9_total"), _row_get(p, "gad7_total"), _row_get(p, "who5_total")
        dt = _utc_to_pk(_parse_dt(_row_get(r, "assessment_date")))
        assessments.append({
            "date": _fmt_date(dt, lang),
            "phq": phq, "gad": gad, "who": who,
            "phq_band": tx(_phq_band(phq), lang), "gad_band": tx(_gad_band(gad), lang), "who_band": tx(_who_band(who), lang),
            "phq_band_en": _phq_band(phq), "gad_band_en": _gad_band(gad), "who_band_en": _who_band(who),
            "phq_level": _band_level(_phq_band(phq)), "gad_level": _band_level(_gad_band(gad)),
            "who_level": _band_level(_who_band(who)),
            "phq_change": _change(phq, prev_phq, lang=lang), "gad_change": _change(gad, prev_gad, lang=lang),
            "who_change": _change(who, prev_who, higher_is_better=True, lang=lang),
            "severity": tx(_row_get(r, "severity", "—"), lang),
        })
    latest_assessment = assessments[-1] if assessments else None
    first_assessment = assessments[0] if assessments else None

    displacement = None
    hardships = []
    for r in reversed(all_assess):
        if not displacement and _row_get(r, "displacement_cause"):
            displacement = DISPLACEMENT_LABELS.get(_row_get(r, "displacement_cause"), str(_row_get(r, "displacement_cause")).title())
        if not hardships and _row_get(r, "idp_hardships"):
            raw = _row_get(r, "idp_hardships")
            try:
                items = json.loads(raw) if isinstance(raw, str) and raw.strip().startswith("[") else str(raw).split(",")
            except ValueError:
                items = str(raw).split(",")
            hardships = [tx(HARDSHIP_LABELS.get(str(x).strip(), str(x).strip().replace("_", " ").title()), lang)
                         for x in items if str(x).strip()]

    # ---------- wellbeing logs ----------
    wb_rows = db.execute(
        "SELECT * FROM wellbeing_logs WHERE user_id = ? ORDER BY log_date ASC, id ASC", (user_id,)
    ).fetchall() if user_id else []
    wellbeing = []
    for r in wb_rows:
        dt = _parse_dt(_row_get(r, "log_date"))
        if since_pk and dt and dt < since_pk - timedelta(hours=5):
            continue
        mood = (_row_get(r, "mood", "") or "").lower()
        energy = (_row_get(r, "energy", "") or "").lower()
        sleep = _row_get(r, "sleep_hours")
        if sleep is None and not mood and not energy:
            continue
        wellbeing.append({
            "date": _fmt_short(dt, lang),
            "sleep": float(sleep) if sleep is not None else None,
            "mood": mood, "energy": energy,
            "mood_score": MOOD_SCORE.get(mood), "energy_score": ENERGY_SCORE.get(energy),
        })

    sleeps = [w["sleep"] for w in wellbeing if w["sleep"] is not None]
    moods = [w["mood"] for w in wellbeing if w["mood"]]
    energies = [w["energy"] for w in wellbeing if w["energy"]]
    mood_counts = Counter(tx(MOOD_LABEL.get(m, m.title()), lang) for m in moods)
    energy_counts = Counter(tx(ENERGY_LABEL.get(e, e.title()), lang) for e in energies)
    wb_summary = {
        "entries": len(wellbeing),
        "avg_sleep": round(sum(sleeps) / len(sleeps), 1) if sleeps else None,
        "min_sleep": min(sleeps) if sleeps else None,
        "max_sleep": max(sleeps) if sleeps else None,
        "short_sleep_nights": sum(1 for s in sleeps if s < 6),
        "sleep_count": len(sleeps),
        "top_mood": mood_counts.most_common(1)[0][0] if mood_counts else None,
        "top_energy": energy_counts.most_common(1)[0][0] if energy_counts else None,
        "mood_counts": mood_counts.most_common(),
        "energy_counts": energy_counts.most_common(),
        "low_mood_share": round(100 * sum(1 for m in moods if MOOD_SCORE.get(m, 3) <= 2) / len(moods)) if moods else None,
    }

    # ---------- chat history ----------
    chat_sql = "SELECT * FROM chat_history WHERE user_id = ?"
    params = [user_id]
    if since_utc_text:
        chat_sql += " AND timestamp >= ?"
        params.append(since_utc_text)
    chat_rows = db.execute(chat_sql + " ORDER BY timestamp ASC, id ASC", params).fetchall() if user_id else []

    user_msgs = []
    for r in chat_rows:
        if (_row_get(r, "sender", "") or "").lower() != "user":
            continue
        text = str(_row_get(r, "message", "") or "").strip()
        if not text:
            continue
        user_msgs.append({"text": text, "dt": _utc_to_pk(_parse_dt(_row_get(r, "timestamp")))})

    active_days = sorted({m["dt"].date() for m in user_msgs if m["dt"]})
    hours = [m["dt"].hour for m in user_msgs if m["dt"]]
    time_buckets = Counter(
        "Morning (5am–12pm)" if 5 <= h < 12 else
        "Afternoon (12–5pm)" if 12 <= h < 17 else
        "Evening (5–10pm)" if 17 <= h < 22 else
        "Late night (10pm–5am)"
        for h in hours
    )
    urdu_count = sum(1 for m in user_msgs if _is_urdu(m["text"]))

    themes = []
    if user_msgs:
        for theme, words in EMOTION_THEMES.items():
            n = sum(1 for m in user_msgs if _count_hits(m["text"], words))
            if n:
                themes.append({"name": tx(theme, lang), "count": n, "pct": round(100 * n / len(user_msgs))})
        for key, words in IDP_HARDSHIP_KEYWORDS.items():
            n = sum(1 for m in user_msgs if _count_hits(m["text"], words))
            if n:
                themes.append({"name": tx(HARDSHIP_LABELS.get(key, key.replace("_", " ").title()), lang), "count": n,
                               "pct": round(100 * n / len(user_msgs))})
        themes.sort(key=lambda t: t["count"], reverse=True)
        themes = themes[:8]

    risk_msgs = [m for m in user_msgs if _RISK_RE.search(m["text"])]
    risk_events = [{
        "date": _fmt_datetime(m["dt"], lang),
        "excerpt": (m["text"][:110] + "…") if len(m["text"]) > 110 else m["text"],
    } for m in risk_msgs[-5:]]
    risk_mem = (_row_get(memory, "risk_pattern", "") or "").strip()

    chat = {
        "user_messages": len(user_msgs),
        "total_messages": len(chat_rows),
        "active_days": len(active_days),
        "first": _fmt_date(user_msgs[0]["dt"], lang) if user_msgs else "—",
        "last": _fmt_date(user_msgs[-1]["dt"], lang) if user_msgs else "—",
        "avg_words": round(sum(len(m["text"].split()) for m in user_msgs) / len(user_msgs)) if user_msgs else 0,
        "language": tx(("Mostly Urdu" if urdu_count > len(user_msgs) * 0.6 else
                        "Mostly English / Roman Urdu" if urdu_count < len(user_msgs) * 0.4 else
                        "Urdu and English"), lang) if user_msgs else "—",
        "time_of_day": tx(time_buckets.most_common(1)[0][0], lang) if time_buckets else "—",
        "themes": themes,
        "risk_count": len(risk_msgs),
        "risk_events": risk_events,
    }

    try:
        stressors = json.loads(_row_get(memory, "main_stressors", "[]") or "[]")
    except ValueError:
        stressors = []

    created = _utc_to_pk(_parse_dt(_row_get(account, "created_at")))
    last_seen = _utc_to_pk(_parse_dt(_row_get(user, "last_seen")))
    profile = {
        "full_name": _row_get(account, "full_name", session.get("name", "")),
        "urdu_name": _row_get(account, "urdu_name", ""),
        "email": _row_get(account, "email", ""),
        "dob": _fmt_date(_parse_dt(_row_get(account, "dob")), lang),
        "age": _age_from_dob(_row_get(account, "dob")),
        "profession": profession,
        "living_place": (living_place or "").strip().title() or tx("Not specified", lang),
        "displaced": tx("Yes" if (_row_get(memory, "is_displaced") or displacement) else "Not stated", lang),
        "displacement": tx(displacement or "Not specified", lang),
        "displacement_known": bool(displacement),
        "profession_known": profession != tx("Not specified", lang),
        "hardships": hardships,
        "stressors": [str(s) for s in stressors][:6],
        "member_since": _fmt_date(created, lang),
        "last_active": _fmt_date(last_seen, lang),
        "extra": extra_fields,
    }

    # ---------- crisis alerts (saved by the chatbot) ----------
    ensure_report_tables()
    since_utc = (since_pk - timedelta(hours=5)) if since_pk else None
    alerts = _crisis_events(db, user_id, since_utc, lang)
    chat["alerts"] = alerts
    chat["alert_count"] = len(alerts)
    chat["emergency_count"] = sum(1 for a in alerts if a["level_key"] == "emergency")
    # older chats were made before alerts were saved: keep the word check for those
    chat["risk_source"] = "alerts" if alerts else "words"
    if alerts:
        chat["risk_count"] = len(alerts)
        chat["risk_events"] = alerts[-5:]

    # ---------- Calm Corner ----------
    calm = _calm_summary(db, user_id, since_utc, lang)

    # ---------- this period vs the period before ----------
    compare = []
    if days:
        cur = _window_metrics(db, user_id, since_utc, None)
        prev = _window_metrics(db, user_id, since_utc - timedelta(days=days), since_utc)
        compare = _comparison(cur, prev, lang)

    fingerprint = (
        len(chat_rows), _row_get(chat_rows[-1], "id") if chat_rows else 0,
        len(wellbeing), len(assessments), _row_get(assess_rows[-1], "id") if assess_rows else 0,
        len(alerts), calm["count"],
    )

    return {
        "user_id": user_id,
        "profile": profile,
        "assessments": assessments,
        "latest": latest_assessment,
        "first_assessment": first_assessment,
        "wellbeing": wellbeing,
        "wb": wb_summary,
        "chat": chat,
        "calm": calm,
        "compare": compare,
        "risk_memory": risk_mem,
        "user_texts": [m["text"] for m in user_msgs],
        "fingerprint": fingerprint,
        "now_pk": now_pk,
        "since_pk": since_pk,
        "lang": lang,
    }


# ─────────────────────────────────────────────
# Extra tables for the report (created automatically the first time).
#   crisis_events : every time the chatbot switched to crisis / emergency mode
#   calm_sessions : every finished Calm Corner exercise (distress before / after)
#   report_log    : every report that was made, so its QR code can be verified
# Nothing else in the app changes.
# ─────────────────────────────────────────────
_TABLES_READY = False


def ensure_report_tables():
    global _TABLES_READY
    if _TABLES_READY:
        return
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS crisis_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, account_id INTEGER,
            level TEXT, excerpt TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS calm_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, account_id INTEGER,
            exercise TEXT, before_score INTEGER, after_score INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS report_log (
            report_id TEXT PRIMARY KEY,
            account_id INTEGER, user_id INTEGER,
            initials TEXT, period_label TEXT, lang TEXT, status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db.commit()
    _TABLES_READY = True


@app.after_request
def _record_crisis_reply(response):
    """When the chatbot answers in crisis / emergency mode, save it for the report.
    It only reads the reply that is already going out, so the chat itself is not changed."""
    try:
        if request.path == "/chat" and request.method == "POST" and response.is_json:
            reply = response.get_json(silent=True) or {}
            level = reply.get("type")
            if level in ("crisis", "emergency") and session.get("user_id"):
                text = str((request.get_json(silent=True) or {}).get("message", "")).strip().replace("\n", " ")
                ensure_report_tables()
                db = get_db()
                db.execute(
                    "INSERT INTO crisis_events (user_id, account_id, level, excerpt) VALUES (?, ?, ?, ?)",
                    (session.get("user_id"), session.get("account_id"), level, text[:200]),
                )
                db.commit()
    except Exception as e:
        print("Crisis event log error:", e)
    return response


CALM_EXERCISES = {
    "calm": ("Calming Breath", "پرسکون سانس"),
    "box": ("Box Breathing", "باکس بریدنگ"),
    "grounding": ("5-4-3-2-1 Grounding", "۵-۴-۳-۲-۱ گراؤنڈنگ"),
    "nightmare": ("After a Bad Dream", "برے خواب کے بعد"),
}


def _rows_in_window(rows, col, start_utc, end_utc):
    """Rows whose time (UTC) falls inside [start, end)."""
    out = []
    for r in rows:
        dt = _parse_dt(_row_get(r, col))
        if dt is None:
            continue
        if start_utc and dt < start_utc:
            continue
        if end_utc and dt >= end_utc:
            continue
        out.append(r)
    return out


def _window_metrics(db, user_id, start_utc, end_utc):
    """The same key numbers for any time window, so two periods can be compared fairly."""
    m = {}
    if not user_id:
        return m
    a = _rows_in_window(db.execute("SELECT * FROM assessments WHERE user_id = ? ORDER BY assessment_date, id",
                                   (user_id,)).fetchall(), "assessment_date", start_utc, end_utc)
    if a:
        m["phq"], m["gad"], m["who"] = (_row_get(a[-1], "phq9_total"), _row_get(a[-1], "gad7_total"),
                                        _row_get(a[-1], "who5_total"))
    w = _rows_in_window(db.execute("SELECT * FROM wellbeing_logs WHERE user_id = ?", (user_id,)).fetchall(),
                        "log_date", start_utc, end_utc)
    sleeps = [float(_row_get(r, "sleep_hours")) for r in w if _row_get(r, "sleep_hours") is not None]
    moods = [(_row_get(r, "mood", "") or "").lower() for r in w if _row_get(r, "mood")]
    if sleeps:
        m["sleep"] = round(sum(sleeps) / len(sleeps), 1)
    if moods:
        m["low_mood"] = round(100 * sum(1 for x in moods if MOOD_SCORE.get(x, 3) <= 2) / len(moods))
    c = _rows_in_window(db.execute("SELECT * FROM chat_history WHERE user_id = ? AND lower(sender) = 'user'",
                                   (user_id,)).fetchall(), "timestamp", start_utc, end_utc)
    m["messages"] = len(c)
    m["active_days"] = len({_utc_to_pk(_parse_dt(_row_get(r, "timestamp"))).date() for r in c})
    ce = _rows_in_window(db.execute("SELECT * FROM crisis_events WHERE user_id = ?", (user_id,)).fetchall(),
                         "created_at", start_utc, end_utc)
    m["crisis"] = len(ce)
    cs = _rows_in_window(db.execute("SELECT * FROM calm_sessions WHERE user_id = ?", (user_id,)).fetchall(),
                         "created_at", start_utc, end_utc)
    m["calm"] = len(cs)
    if cs:
        m["relief"] = round(sum((_row_get(r, "before_score") or 0) - (_row_get(r, "after_score") or 0)
                                for r in cs) / len(cs), 1)
    return m


# label EN, label UR, higher_is_better (None = neither), unit
COMPARE_ROWS = [
    ("phq", "Depression (PHQ-9)", "ڈپریشن (PHQ-9)", False, ""),
    ("gad", "Anxiety (GAD-7)", "بے چینی (GAD-7)", False, ""),
    ("who", "Wellbeing (WHO-5)", "فلاح (WHO-5)", True, ""),
    ("sleep", "Average sleep (hours)", "اوسط نیند (گھنٹے)", True, ""),
    ("low_mood", "Low / anxious mood entries", "کم / بے چین موڈ کے اندراجات", False, "%"),
    ("messages", "Messages to the chatbot", "چیٹ بوٹ کو پیغامات", None, ""),
    ("active_days", "Active days", "فعال دن", None, ""),
    ("calm", "Calm Corner exercises", "پرسکون گوشہ کی مشقیں", None, ""),
    ("relief", "Average relief per exercise (points)", "فی مشق اوسط سکون (پوائنٹس)", True, ""),
    ("crisis", "Crisis alerts", "ہنگامی الرٹس", False, ""),
]


def _comparison(cur, prev, lang):
    ur = lang == "ur"
    rows = []
    for key, en, urd, higher_better, unit in COMPARE_ROWS:
        a, b = cur.get(key), prev.get(key)
        if a is None and b is None:
            continue
        if key in ("messages", "active_days", "calm", "crisis") and not a and not b:
            continue
        fa = "—" if a is None else f"{a:g}{unit}"
        fb = "—" if b is None else f"{b:g}{unit}"
        if a is None or b is None:
            text, cls = ("موازنہ ممکن نہیں" if ur else "Not comparable"), "same"
        else:
            diff = round(a - b, 1)
            if diff == 0:
                text, cls = ("کوئی تبدیلی نہیں" if ur else "No change"), "same"
            else:
                arrow = "▲" if diff > 0 else "▼"
                amount = f"{arrow} {abs(diff):g}{unit}"
                if higher_better is None:
                    text, cls = amount, "same"
                else:
                    good = (diff > 0) == higher_better
                    word = ("بہتر" if good else "بدتر") if ur else ("better" if good else "worse")
                    text, cls = f"{amount} ({word})", ("good" if good else "bad")
        rows.append({"label": urd if ur else en, "now": fa, "prev": fb, "text": text, "cls": cls})
    return rows


def _calm_summary(db, user_id, start_utc, lang):
    ur = lang == "ur"
    rows = _rows_in_window(db.execute("SELECT * FROM calm_sessions WHERE user_id = ? ORDER BY created_at, id",
                                      (user_id,)).fetchall(), "created_at", start_utc, None) if user_id else []
    sessions = []
    for r in rows:
        b, a = _row_get(r, "before_score"), _row_get(r, "after_score")
        name = CALM_EXERCISES.get(_row_get(r, "exercise"), (_row_get(r, "exercise"), _row_get(r, "exercise")))
        sessions.append({
            "date": _fmt_datetime(_utc_to_pk(_parse_dt(_row_get(r, "created_at"))), lang),
            "exercise": name[1] if ur else name[0],
            "before": b, "after": a, "relief": (b or 0) - (a or 0),
        })
    if not sessions:
        return {"count": 0, "sessions": []}
    n = len(sessions)
    by_ex = Counter(s["exercise"] for s in sessions)
    return {
        "count": n,
        "avg_before": round(sum(s["before"] or 0 for s in sessions) / n, 1),
        "avg_after": round(sum(s["after"] or 0 for s in sessions) / n, 1),
        "avg_relief": round(sum(s["relief"] for s in sessions) / n, 1),
        "helped_pct": round(100 * sum(1 for s in sessions if s["relief"] > 0) / n),
        "by_exercise": by_ex.most_common(),
        "top": by_ex.most_common(1)[0][0],
        "sessions": sessions[-8:],
    }


def _crisis_events(db, user_id, start_utc, lang):
    rows = _rows_in_window(db.execute("SELECT * FROM crisis_events WHERE user_id = ? ORDER BY created_at, id",
                                      (user_id,)).fetchall(), "created_at", start_utc, None) if user_id else []
    level_names = {"emergency": ("Emergency", "ایمرجنسی"), "crisis": ("Crisis", "بحران")}
    return [{
        "date": _fmt_datetime(_utc_to_pk(_parse_dt(_row_get(r, "created_at"))), lang),
        "level": level_names.get(_row_get(r, "level"), ("Crisis", "بحران"))[1 if lang == "ur" else 0],
        "level_key": _row_get(r, "level") or "crisis",
        "excerpt": (_row_get(r, "excerpt") or "")[:110] + ("…" if len(_row_get(r, "excerpt") or "") > 110 else ""),
    } for r in rows]


def _initials(name):
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    return ". ".join(p[0].upper() for p in parts[:3]) + "." if parts else "—"


# ─────────────────────────────────────────────
# Overall status (rule based, so it is consistent and explainable)
# ─────────────────────────────────────────────
def _overall_status(data):
    ur = data.get("lang") == "ur"
    latest = data["latest"] or {}
    phq, gad, who = latest.get("phq"), latest.get("gad"), latest.get("who")
    wb, chat = data["wb"], data["chat"]

    def R(en, urdu):
        return urdu if ur else en

    reasons = []
    if chat.get("alert_count"):
        reasons.append(R(f'the chatbot raised {chat["alert_count"]} crisis alert(s) in this period',
                         f'اس عرصے میں چیٹ بوٹ نے {chat["alert_count"]} ہنگامی الرٹ دیے'))
    elif chat["risk_count"]:
        reasons.append(R(f'{chat["risk_count"]} message(s) with signs of suicidal thoughts or self-harm in this period',
                         f'اس عرصے میں {chat["risk_count"]} پیغام(ات) میں خودکشی یا خود کو نقصان پہنچانے کے آثار'))
    if phq is not None and phq >= 20:
        reasons.append(R(f"PHQ-9 score of {phq} (severe range)", f"PHQ-9 اسکور {phq} (شدید درجہ)"))
    if reasons:
        return {"key": "urgent", "label": R("Urgent attention recommended", "فوری توجہ کی ضرورت ہے"), "reasons": reasons}

    if phq is not None and phq >= 15:
        reasons.append(R(f"PHQ-9 score of {phq} (moderately severe)", f"PHQ-9 اسکور {phq} (کافی شدید)"))
    if gad is not None and gad >= 15:
        reasons.append(R(f"GAD-7 score of {gad} (severe anxiety range)", f"GAD-7 اسکور {gad} (شدید بے چینی)"))
    if who is not None and who <= 7:
        reasons.append(R(f"WHO-5 score of {who} (very low wellbeing)", f"WHO-5 اسکور {who} (بہت کم فلاح)"))
    if reasons:
        return {"key": "attention", "label": R("Needs professional attention", "ماہر کی توجہ درکار ہے"), "reasons": reasons}

    if phq is not None and phq >= 10:
        reasons.append(R(f"PHQ-9 score of {phq} (moderate)", f"PHQ-9 اسکور {phq} (درمیانہ)"))
    if gad is not None and gad >= 10:
        reasons.append(R(f"GAD-7 score of {gad} (moderate)", f"GAD-7 اسکور {gad} (درمیانہ)"))
    if (wb["low_mood_share"] or 0) >= 50:
        reasons.append(R(f'low or anxious mood in {wb["low_mood_share"]}% of daily entries',
                         f'{wb["low_mood_share"]}% روزانہ اندراجات میں کم یا بے چین موڈ'))
    if wb["avg_sleep"] is not None and wb["avg_sleep"] < 5:
        reasons.append(R(f'average sleep of {wb["avg_sleep"]:g} hours', f'اوسط نیند {wb["avg_sleep"]:g} گھنٹے'))
    if reasons:
        return {"key": "monitor", "label": R("Monitor closely", "قریبی نگرانی کریں"), "reasons": reasons}

    if not data["latest"] and not data["wellbeing"] and not chat["user_messages"]:
        return {"key": "nodata", "label": R("Not enough data yet", "ابھی کافی معلومات نہیں"),
                "reasons": [R("no assessments, wellbeing entries or conversations in this period",
                              "اس عرصے میں کوئی جائزہ، فلاح کا اندراج یا گفتگو نہیں")]}
    return {"key": "stable", "label": R("Stable", "مستحکم"),
            "reasons": [R("no high-risk indicators in this period", "اس عرصے میں کوئی خطرے کی علامت نہیں")]}


# ─────────────────────────────────────────────
# Professional narrative (AI with a rule-based fallback)
# ─────────────────────────────────────────────
REPORT_PROMPT = """You are a clinical psychologist writing the narrative part of a mental-wellbeing progress report
about an internally displaced person (IDP) in Pakistan who uses a support chatbot called Empath AI.
The reader is a doctor, counsellor or NGO case worker. Write in clear, plain, professional English, third person
("the client"), calm and respectful. Use ONLY the data given. Never invent facts, names, events or numbers.
Do NOT give a diagnosis; describe observations and screening results. If data is limited, say so honestly.
The client's messages may be in Urdu or Roman Urdu: understand them, but write the report in English and do not
quote more than a few words.

Return ONLY JSON with these keys:
{
 "executive_summary": "3-4 sentences: who the client is, overall picture, most important finding, direction of change",
 "presenting_concerns": ["3-5 short bullet points"],
 "emotional_themes": "2-3 sentences on recurring feelings seen in conversations",
 "stressors": ["2-5 short bullet points on life stressors, e.g. displacement, income, family"],
 "strengths": ["2-4 short bullet points on coping, protective factors, engagement"],
 "risk_assessment": "2-3 sentences on safety, based on the risk data given (state clearly if no risk indicators were found)",
 "progress": "2 sentences comparing earlier and later data, using compared_with_previous_period and calm_corner_exercises when given",
 "recommendations": ["4-6 concrete, practical next steps for the professional and the client"]
}"""


URDU_INSTRUCTION = """
LANGUAGE: Write ALL the JSON values in clear, simple, respectful Pakistani Urdu (Urdu script), suitable for
the client and their family to read. Keep the JSON keys in English. Keep test names (PHQ-9, GAD-7, WHO-5)
and numbers as they are. Do not use Roman Urdu."""


def _fallback_narrative(data, status):
    if data.get("lang") == "ur":
        return _fallback_narrative_ur(data, status)
    p, wb, chat, latest = data["profile"], data["wb"], data["chat"], data["latest"]
    parts = [f'The client is a {p["age"]}-year-old' if p["age"] else "The client is an adult",
             f'{p["profession"].lower()}' if p["profession"] != "Not specified" else "individual"]
    summary = " ".join(parts) + f' affected by {p["displacement"].lower()}.' if p["displacement"] != "Not specified" else " ".join(parts) + "."
    if latest:
        summary += (f' The most recent screening shows {latest["phq_band"].lower()} depressive symptoms (PHQ-9: {latest["phq"]}), '
                    f'{latest["gad_band"].lower()} anxiety (GAD-7: {latest["gad"]}) and {latest["who_band"].lower()} (WHO-5: {latest["who"]}).')
    if wb["avg_sleep"] is not None:
        summary += f' Average reported sleep was {wb["avg_sleep"]:g} hours.'
    summary += f' Overall status: {status["label"].lower()}.'
    return {
        "executive_summary": summary,
        "presenting_concerns": [t["name"] for t in chat["themes"][:4]] or ["Not enough conversation data in this period"],
        "emotional_themes": ("Conversations most often touched on " + ", ".join(t["name"].lower() for t in chat["themes"][:3]) + ".")
        if chat["themes"] else "Not enough conversation data to describe emotional themes.",
        "stressors": data["profile"]["hardships"] or data["profile"]["stressors"] or ["Not specified in this period"],
        "strengths": ["Continues to engage with the support service"] + (["Completed standardised self-assessments"] if latest else []),
        "risk_assessment": ("Signs of suicidal thoughts or self-harm were detected in the client's messages. A direct safety assessment is advised."
                            if chat["risk_count"] else "No signs of suicidal thoughts or self-harm were detected in this period."),
        "progress": "Compare the assessment table and charts in this report for change over time.",
        "recommendations": [
            "Review this report with the client in a face-to-face session.",
            "Continue weekly self-assessments to track change.",
            "Encourage regular sleep and daily routine where possible.",
            "Share Umang (0311-7786264) and 1122 for urgent support.",
        ],
        "ai": False,
    }


def _fallback_narrative_ur(data, status):
    p, wb, chat, latest = data["profile"], data["wb"], data["chat"], data["latest"]
    who = f'{p["age"]} سالہ' if p["age"] else "بالغ"
    summary = f"یہ رپورٹ ایک {who} فرد کے بارے میں ہے"
    if p.get("profession_known"):
        summary += f" ({p['profession']})"
    if p.get("displacement_known"):
        summary += f" جو {p['displacement']} کی وجہ سے بے گھر ہوئے"
    summary += "۔"
    if latest:
        summary += (f' حالیہ جائزے کے مطابق ڈپریشن کی علامات {latest["phq_band"]} (PHQ-9: {latest["phq"]})، '
                    f'بے چینی {latest["gad_band"]} (GAD-7: {latest["gad"]}) اور مجموعی فلاح {latest["who_band"]} (WHO-5: {latest["who"]}) ہے۔')
    if wb["avg_sleep"] is not None:
        summary += f' اوسط نیند {wb["avg_sleep"]:g} گھنٹے رہی۔'
    summary += f' مجموعی حالت: {status["label"]}۔'
    return {
        "executive_summary": summary,
        "presenting_concerns": [t["name"] for t in chat["themes"][:4]] or ["اس عرصے میں گفتگو کی کافی معلومات نہیں"],
        "emotional_themes": ("گفتگو میں زیادہ تر " + "، ".join(t["name"] for t in chat["themes"][:3]) + " کا ذکر آیا۔")
        if chat["themes"] else "جذباتی موضوعات بیان کرنے کے لیے گفتگو کی کافی معلومات نہیں۔",
        "stressors": p["hardships"] or p["stressors"] or ["اس عرصے میں درج نہیں"],
        "strengths": ["مدد کی سروس سے مسلسل رابطے میں ہیں"] + (["معیاری خود تشخیصی جائزے مکمل کیے"] if latest else []),
        "risk_assessment": ("پیغامات میں خودکشی یا خود کو نقصان پہنچانے کے خیالات کے آثار ملے۔ براہِ راست حفاظتی جائزہ تجویز کیا جاتا ہے۔"
                            if chat["risk_count"] else "اس عرصے میں خودکشی یا خود کو نقصان پہنچانے کے خیالات کے کوئی آثار نہیں ملے۔"),
        "progress": "وقت کے ساتھ تبدیلی دیکھنے کے لیے اس رپورٹ کے جدول اور گراف دیکھیں۔",
        "recommendations": [
            "اس رپورٹ پر کسی ماہر کے ساتھ آمنے سامنے بیٹھ کر بات کریں۔",
            "تبدیلی جاننے کے لیے ہر ہفتے خود تشخیصی جائزہ جاری رکھیں۔",
            "جہاں تک ممکن ہو باقاعدہ نیند اور روزمرہ کا معمول رکھیں۔",
            "فوری مدد کے لیے اُمنگ (0311-7786264) اور 1122 سے رابطہ کریں۔",
        ],
        "ai": False,
    }


def _ai_narrative(data, status, period_label):
    key = (data["user_id"], period_label, data["fingerprint"], data.get("lang", "en"))
    if key in _AI_CACHE:
        return _AI_CACHE[key]
    if not os.environ.get("OPENAI_API_KEY") or (not data["user_texts"] and not data["assessments"] and not data["wellbeing"]):
        return _fallback_narrative(data, status)

    p, wb, chat = data["profile"], data["wb"], data["chat"]
    facts = {
        "period": period_label,
        "client": {"age": p["age"], "profession": p["profession"], "living_situation": p["living_place"],
                   "displacement_cause": p["displacement"], "hardships": p["hardships"], "stressors_noted": p["stressors"]},
        "overall_status_rule_based": status,
        "assessments_in_period": [
            {"date": a["date"], "PHQ9": a["phq"], "PHQ9_band": a["phq_band"], "GAD7": a["gad"], "GAD7_band": a["gad_band"],
             "WHO5": a["who"], "WHO5_band": a["who_band"]} for a in data["assessments"][-6:]
        ],
        "daily_wellbeing": {k: wb[k] for k in ("entries", "avg_sleep", "min_sleep", "max_sleep", "short_sleep_nights",
                                                "top_mood", "top_energy", "mood_counts", "energy_counts", "low_mood_share")},
        "conversation_stats": {k: chat[k] for k in ("user_messages", "active_days", "avg_words", "language", "time_of_day")},
        "themes_detected": chat["themes"],
        "risk_messages_count": chat["risk_count"],
        "crisis_alerts_raised_by_chatbot": chat.get("alert_count", 0),
        "calm_corner_exercises": {k: data["calm"].get(k) for k in ("count", "avg_before", "avg_after", "avg_relief",
                                                                  "helped_pct", "top")},
        "compared_with_previous_period": [{"metric": r["label"], "now": r["now"], "before": r["prev"],
                                           "change": r["text"]} for r in data["compare"]],
        "risk_note_from_memory": data["risk_memory"],
    }
    # A sample of the client's own words (recent first), kept short
    sample, used = [], 0
    for text in reversed(data["user_texts"]):
        t = text.replace("\n", " ")[:220]
        if used + len(t) > 5000:
            break
        sample.append(t)
        used += len(t)
    sample.reverse()

    content = "DATA:\n" + json.dumps(facts, ensure_ascii=False, default=str)
    content += "\n\nCLIENT MESSAGES (oldest to newest, sample):\n" + "\n".join(f"- {s}" for s in sample)

    try:
        result = client.chat.completions.create(
            model=os.environ.get("EMPATH_AI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system",
                       "content": REPORT_PROMPT + (URDU_INSTRUCTION if data.get("lang") == "ur" else "")},
                      {"role": "user", "content": content}],
            temperature=0.3,
            max_tokens=1100 if data.get("lang") != "ur" else 1800,
            response_format={"type": "json_object"},
        )
        out = json.loads(result.choices[0].message.content or "{}")
    except Exception as e:
        print("Report AI error (using rule-based summary):", e)
        return _fallback_narrative(data, status)

    base = _fallback_narrative(data, status)
    narrative = {"ai": True}
    for k, default in base.items():
        if k == "ai":
            continue
        v = out.get(k)
        if isinstance(default, list):
            v = [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []
            narrative[k] = v[:6] or default
        else:
            narrative[k] = str(v).strip() if v else default
    _AI_CACHE[key] = narrative
    return narrative


# ─────────────────────────────────────────────
# Fixed words on the report, in English and Urdu
# ─────────────────────────────────────────────
REPORT_TEXT = {
    "en": dict(
        brand_sub="Mental Health Support for Internally Displaced People · Pakistan",
        report_id="Report ID", printed_on="Printed on", pkt="(PKT)", prepared_for="Prepared for",
        prepared_for_value="Doctor / Counsellor / Case worker",
        title="Mental Wellbeing Progress Report", period="Reporting period",
        confidential="CONFIDENTIAL · FOR PROFESSIONAL USE",
        overall_status="Overall status", based_on="Based on", sep="; ", stop=".",
        s1="Client Information", full_name="Full name", urdu_name="Name (Urdu)", age="Age", years="years",
        dob="Date of birth", profession="Profession / role", email="Email", living="Current living situation",
        displacement="Displacement cause", member_since="Member since", last_active="Last active",
        hardships="Reported hardships",
        s2="Executive Summary", ai_tag="AI-ASSISTED",
        s3="Key Indicators", phq_label="Depression (PHQ-9)", gad_label="Anxiety (GAD-7)", who_label="Wellbeing (WHO-5)",
        no_assessment="No assessment in period", avg_sleep="Average sleep", hours_short="h",
        under6="{a} of {b} records under 6 hours", not_reported="Not reported",
        top_mood="Most frequent mood", low_share="Low or anxious in {p}% of entries",
        engagement="Engagement", days_one="day", days_many="days",
        msgs_assess="{m} messages · {a} assessments",
        s4="Standardised Screening Results",
        s4_note="PHQ-9 (depression, 0–27, lower is better) · GAD-7 (anxiety, 0–21, lower is better) · WHO-5 (wellbeing, 0–25, higher is better). Change is compared with the previous assessment.",
        date="Date", severity="Overall severity", scores_chart="Screening scores over time",
        no_assessments="No self-assessment was completed in this period.",
        s5="Daily Wellbeing (from conversations)",
        s5_note="Sleep, mood and energy are read automatically from what the client writes in chat. {n} daily records in this period.",
        sleep_chart="Sleep (hours)", mood_chart="Mood and energy", mood_dist="Mood distribution",
        energy_dist="Energy distribution", not_reported_dot="Not reported.",
        no_wellbeing="No sleep, mood or energy information was shared in this period.",
        s6="Conversation Analysis", c_msgs="Messages from client", c_days="Active days", c_first="First / last",
        c_len="Avg. length", c_lang="Language", c_time="Usual time", words="words",
        themes="Main themes in the client's messages", no_themes="No clear themes detected.",
        themes_legend="% = share of the client's messages that mention the theme",
        concerns="Presenting concerns", emotional="Emotional themes", stressors="Life stressors",
        strengths="Strengths and coping", progress="Progress during the period",
        no_chat="The client did not use the chat in this period.",
        s7="Risk and Safety", risk_count="{n} message(s) with possible suicidal thoughts or self-harm. Most recent:",
        emergency="Emergency contacts shared with the client: Rescue 1122 · Umang 0311-7786264 · Rozan 051-2890505.",
        s8="Recommendations", reviewed_by="Reviewed by (name, signature)", review_date="Date of review",
        important="Important:",
        disclaimer="This report was generated automatically by Empath AI from the client's own self-assessments and conversations. It is a screening and monitoring summary, <b>not a medical diagnosis</b>. PHQ-9, GAD-7 and WHO-5 are validated screening tools; scores should be interpreted by a qualified professional together with a clinical interview.",
        disclaimer_ai="Sections marked “AI-assisted” were written with the help of a language model and should be verified.",
        footer_line="Report {id} · printed {d} (Pakistan Standard Time).",
        chart_mood="Mood", chart_energy="Energy", chart_phq="PHQ-9 (depression)", chart_gad="GAD-7 (anxiety)",
        chart_who="WHO-5 (wellbeing)", lv1="Very low", lv2="Low", lv3="Okay / Medium", lv4="Good / High",
        page="Page", of="of", confidential_short="Confidential",
        cmp_title="Compared with the previous period", cmp_note="The same number of days just before this period.",
        cmp_metric="Measure", cmp_now="This period", cmp_prev="Previous period", cmp_change="Change",
        s_calm="Calm Corner Exercises",
        calm_note="Guided breathing and grounding exercises. Before and after each exercise the client rates their distress from 0 (calm) to 10 (very distressed).",
        calm_count="Exercises done", calm_before="Avg. distress before", calm_after="Avg. distress after",
        calm_relief="Avg. relief", calm_helped="Felt better in", calm_points="points", calm_top="Most used",
        calm_exercise="Exercise", calm_b="Before", calm_a="After", calm_change="Change",
        no_calm="No Calm Corner exercise was completed in this period.",
        alerts_count="The chatbot switched to crisis support {n} time(s) in this period ({e} emergency). Each time it shared helpline numbers. Most recent:",
        alerts_level="Level", risk_words_note="Found by checking the words of the client's messages.",
        verify_title="Scan to verify", verify_note="Scan this code to check that this report is genuine.",
    ),
    "ur": dict(
        brand_sub="اندرونِ ملک بے گھر افراد کے لیے ذہنی صحت کی مدد · پاکستان",
        report_id="رپورٹ نمبر", printed_on="پرنٹ کی تاریخ", pkt="(پاکستانی وقت)", prepared_for="کس کے لیے",
        prepared_for_value="ڈاکٹر / کونسلر / کیس ورکر",
        title="ذہنی فلاح و بہبود کی پیش رفت رپورٹ", period="رپورٹ کا عرصہ",
        confidential="خفیہ · صرف ماہرین کے لیے",
        overall_status="مجموعی حالت", based_on="بنیاد", sep="؛ ", stop="۔",
        s1="ذاتی معلومات", full_name="پورا نام", urdu_name="نام (اردو)", age="عمر", years="سال",
        dob="تاریخِ پیدائش", profession="پیشہ / کردار", email="ای میل", living="موجودہ رہائش",
        displacement="بے گھر ہونے کی وجہ", member_since="رکنیت کی تاریخ", last_active="آخری بار فعال",
        hardships="بتائی گئی مشکلات",
        s2="خلاصہ", ai_tag="اے آئی کی مدد سے",
        s3="اہم اشارے", phq_label="ڈپریشن (PHQ-9)", gad_label="بے چینی (GAD-7)", who_label="فلاح (WHO-5)",
        no_assessment="اس عرصے میں کوئی جائزہ نہیں", avg_sleep="اوسط نیند", hours_short="گھنٹے",
        under6="{b} میں سے {a} اندراجات میں نیند ۶ گھنٹے سے کم", not_reported="درج نہیں",
        top_mood="سب سے عام موڈ", low_share="{p}% اندراجات میں کم یا بے چین",
        engagement="شمولیت", days_one="دن", days_many="دن",
        msgs_assess="{m} پیغامات · {a} جائزے",
        s4="معیاری جائزوں کے نتائج",
        s4_note="PHQ-9 (ڈپریشن، ۰ سے ۲۷، کم بہتر ہے) · GAD-7 (بے چینی، ۰ سے ۲۱، کم بہتر ہے) · WHO-5 (فلاح، ۰ سے ۲۵، زیادہ بہتر ہے)۔ تبدیلی کا موازنہ پچھلے جائزے سے کیا گیا ہے۔",
        date="تاریخ", severity="مجموعی شدت", scores_chart="وقت کے ساتھ جائزوں کے اسکور",
        no_assessments="اس عرصے میں کوئی خود تشخیصی جائزہ مکمل نہیں ہوا۔",
        s5="روزانہ کی فلاح (گفتگو سے)",
        s5_note="نیند، موڈ اور توانائی گفتگو میں لکھی گئی باتوں سے خودکار طور پر لی گئی ہیں۔ اس عرصے میں {n} روزانہ اندراجات۔",
        sleep_chart="نیند (گھنٹے)", mood_chart="موڈ اور توانائی", mood_dist="موڈ کی تقسیم",
        energy_dist="توانائی کی تقسیم", not_reported_dot="درج نہیں۔",
        no_wellbeing="اس عرصے میں نیند، موڈ یا توانائی کے بارے میں کوئی معلومات نہیں دی گئیں۔",
        s6="گفتگو کا تجزیہ", c_msgs="پیغامات کی تعداد", c_days="فعال دن", c_first="پہلی / آخری",
        c_len="اوسط لمبائی", c_lang="زبان", c_time="عام وقت", words="الفاظ",
        themes="پیغامات کے اہم موضوعات", no_themes="کوئی واضح موضوع نہیں ملا۔",
        themes_legend="% = پیغامات کا وہ حصہ جن میں اس موضوع کا ذکر آیا",
        concerns="اہم مسائل", emotional="جذباتی موضوعات", stressors="زندگی کے دباؤ",
        strengths="خوبیاں اور مقابلے کی صلاحیت", progress="اس عرصے میں پیش رفت",
        no_chat="اس عرصے میں گفتگو استعمال نہیں کی گئی۔",
        s7="خطرہ اور حفاظت", risk_count="{n} پیغام(ات) میں خودکشی یا خود کو نقصان پہنچانے کے ممکنہ خیالات۔ حالیہ:",
        emergency="ہنگامی رابطے: ریسکیو 1122 · اُمنگ 0311-7786264 · روزن 051-2890505۔",
        s8="سفارشات", reviewed_by="جائزہ لینے والا (نام، دستخط)", review_date="جائزے کی تاریخ",
        important="اہم:",
        disclaimer="یہ رپورٹ ایمپاتھ اے آئی نے صارف کے اپنے جائزوں اور گفتگو سے خودکار طور پر تیار کی ہے۔ یہ اسکریننگ اور نگرانی کا خلاصہ ہے، <b>طبی تشخیص نہیں</b>۔ PHQ-9، GAD-7 اور WHO-5 مستند اسکریننگ ٹولز ہیں؛ ان کے اسکورز کی تشریح کسی مستند ماہر کو بالمشافہ ملاقات کے ساتھ کرنی چاہیے۔",
        disclaimer_ai="\"اے آئی کی مدد سے\" والے حصے ایک لینگویج ماڈل کی مدد سے لکھے گئے ہیں اور ان کی تصدیق ضروری ہے۔",
        footer_line="رپورٹ {id} · پرنٹ {d} (پاکستانی وقت)۔",
        chart_mood="موڈ", chart_energy="توانائی", chart_phq="PHQ-9 (ڈپریشن)", chart_gad="GAD-7 (بے چینی)",
        chart_who="WHO-5 (فلاح)", lv1="بہت کم", lv2="کم", lv3="ٹھیک / درمیانہ", lv4="اچھا / زیادہ",
        page="صفحہ", of="از", confidential_short="خفیہ",
        cmp_title="پچھلے عرصے سے موازنہ", cmp_note="اس عرصے سے پہلے کے اتنے ہی دن۔",
        cmp_metric="پیمانہ", cmp_now="یہ عرصہ", cmp_prev="پچھلا عرصہ", cmp_change="تبدیلی",
        s_calm="پرسکون گوشہ کی مشقیں",
        calm_note="سانس اور گراؤنڈنگ کی رہنمائی والی مشقیں۔ ہر مشق سے پہلے اور بعد صارف اپنی پریشانی ۰ (پرسکون) سے ۱۰ (بہت پریشان) تک بتاتا ہے۔",
        calm_count="مکمل مشقیں", calm_before="پہلے اوسط پریشانی", calm_after="بعد میں اوسط پریشانی",
        calm_relief="اوسط سکون", calm_helped="بہتر محسوس کیا", calm_points="پوائنٹس", calm_top="سب سے زیادہ",
        calm_exercise="مشق", calm_b="پہلے", calm_a="بعد", calm_change="تبدیلی",
        no_calm="اس عرصے میں پرسکون گوشہ کی کوئی مشق مکمل نہیں ہوئی۔",
        alerts_count="اس عرصے میں چیٹ بوٹ نے {n} بار ہنگامی مدد شروع کی ({e} ایمرجنسی)۔ ہر بار ہیلپ لائن نمبر دیے گئے۔ حالیہ:",
        alerts_level="درجہ", risk_words_note="یہ صارف کے پیغامات کے الفاظ دیکھ کر معلوم کیا گیا۔",
        verify_title="تصدیق کے لیے اسکین کریں", verify_note="یہ کوڈ اسکین کر کے دیکھیں کہ رپورٹ اصلی ہے۔",
    ),
}


# ─────────────────────────────────────────────
# Route
# ─────────────────────────────────────────────
@app.route("/report")
@login_required
def wellbeing_report():
    period = request.args.get("period", "30")
    if period not in REPORT_PERIODS:
        period = "30"
    days = REPORT_PERIODS[period]

    # Report language: ?lang=ur / ?lang=en, otherwise the app language
    lang = request.args.get("lang") or session.get("language") or "en"
    lang = "ur" if str(lang).lower().startswith("ur") else "en"

    data = _collect(session.get("account_id"), days, lang)
    status = _overall_status(data)
    now_pk = data["now_pk"]
    if lang == "ur":
        period_label = (f'{_fmt_date(data["since_pk"], lang)} – {_fmt_date(now_pk, lang)} (پچھلے {days} دن)'
                        if days else f'{_fmt_date(now_pk, lang)} تک کا تمام ریکارڈ')
    else:
        period_label = (f'{_fmt_date(data["since_pk"])} – {_fmt_date(now_pk)} (last {days} days)'
                        if days else f'All records up to {_fmt_date(now_pk)}')
    narrative = _ai_narrative(data, status, period_label)

    report_id = (f'EAI-{now_pk.strftime("%Y%m%d")}-{(data["user_id"] or 0):04d}-'
                 + "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(4)))
    try:
        db = get_db()
        db.execute(
            "INSERT OR REPLACE INTO report_log (report_id, account_id, user_id, initials, period_label, lang, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (report_id, session.get("account_id"), data["user_id"], _initials(data["profile"]["full_name"]),
             period_label, lang, status["key"]),
        )
        db.commit()
    except Exception as e:
        print("Report log error:", e)
    verify_url = request.url_root.rstrip("/") + "/report/verify/" + report_id
    safe_name = re.sub(r"[^A-Za-z0-9]+", "_", data["profile"]["full_name"] or "Client").strip("_") or "Client"

    charts = {
        "wb_labels": [w["date"] for w in data["wellbeing"]],
        "sleep": [w["sleep"] for w in data["wellbeing"]],
        "mood": [w["mood_score"] for w in data["wellbeing"]],
        "energy": [w["energy_score"] for w in data["wellbeing"]],
        "as_labels": [a["date"] for a in data["assessments"]],
        "phq": [a["phq"] for a in data["assessments"]],
        "gad": [a["gad"] for a in data["assessments"]],
        "who": [a["who"] for a in data["assessments"]],
    }

    return render_template(
        "report.html",
        d=data,
        status=status,
        n=narrative,
        charts=charts,
        period=period,
        period_label=period_label,
        printed_on=_fmt_datetime(now_pk, lang) if lang == "ur" else now_pk.strftime("%d %B %Y, %I:%M %p"),
        rlang=lang,
        T=REPORT_TEXT[lang],
        report_id=report_id,
        verify_url=verify_url,
        file_name=f'Empath_AI_Report_{safe_name}_{now_pk.strftime("%Y-%m-%d")}' + ("_Urdu" if lang == "ur" else ""),
    )


@app.route("/report/verify/<report_id>")
def verify_report(report_id):
    """Opened from the QR code on a printed report. Public on purpose (a doctor can scan it),
    so it shows only that the report is genuine: no health information, only initials."""
    row = None
    try:
        ensure_report_tables()
        row = get_db().execute("SELECT * FROM report_log WHERE report_id = ?", (report_id.strip(),)).fetchone()
    except Exception as e:
        print("Report verify error:", e)
    lang = _row_get(row, "lang", "en") if row else ("ur" if str(session.get("language", "")).startswith("ur") else "en")
    made = _utc_to_pk(_parse_dt(_row_get(row, "created_at"))) if row else None
    return render_template(
        "report_verify.html",
        ok=row is not None,
        report_id=report_id,
        initials=_row_get(row, "initials", "—") if row else "",
        period_label=_row_get(row, "period_label", "") if row else "",
        made_on=(_fmt_datetime(made, lang) if lang == "ur" else made.strftime("%d %B %Y, %I:%M %p")) if made else "",
        vlang=lang,
    )


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .core import client  # noqa: E402
from .database import get_db, get_user_by_account_id  # noqa: E402
from .context import IDP_HARDSHIP_KEYWORDS  # noqa: E402
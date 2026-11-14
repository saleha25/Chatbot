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


def _fmt_date(dt):
    return dt.strftime("%d %b %Y") if dt else "—"


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


def _change(curr, prev, higher_is_better=False):
    if curr is None or prev is None:
        return {"text": "—", "dir": "none"}
    diff = curr - prev
    if diff == 0:
        return {"text": "No change", "dir": "same"}
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
def _collect(account_id, days):
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
            "date": _fmt_date(dt),
            "phq": phq, "gad": gad, "who": who,
            "phq_band": _phq_band(phq), "gad_band": _gad_band(gad), "who_band": _who_band(who),
            "phq_level": _band_level(_phq_band(phq)), "gad_level": _band_level(_gad_band(gad)),
            "who_level": _band_level(_who_band(who)),
            "phq_change": _change(phq, prev_phq), "gad_change": _change(gad, prev_gad),
            "who_change": _change(who, prev_who, higher_is_better=True),
            "severity": _row_get(r, "severity", "—"),
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
            hardships = [HARDSHIP_LABELS.get(str(x).strip(), str(x).strip().replace("_", " ").title()) for x in items if str(x).strip()]

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
            "date": dt.strftime("%d %b") if dt else "",
            "sleep": float(sleep) if sleep is not None else None,
            "mood": mood, "energy": energy,
            "mood_score": MOOD_SCORE.get(mood), "energy_score": ENERGY_SCORE.get(energy),
        })

    sleeps = [w["sleep"] for w in wellbeing if w["sleep"] is not None]
    moods = [w["mood"] for w in wellbeing if w["mood"]]
    energies = [w["energy"] for w in wellbeing if w["energy"]]
    mood_counts = Counter(MOOD_LABEL.get(m, m.title()) for m in moods)
    energy_counts = Counter(ENERGY_LABEL.get(e, e.title()) for e in energies)
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
                themes.append({"name": theme, "count": n, "pct": round(100 * n / len(user_msgs))})
        for key, words in IDP_HARDSHIP_KEYWORDS.items():
            n = sum(1 for m in user_msgs if _count_hits(m["text"], words))
            if n:
                themes.append({"name": HARDSHIP_LABELS.get(key, key.replace("_", " ").title()), "count": n,
                               "pct": round(100 * n / len(user_msgs))})
        themes.sort(key=lambda t: t["count"], reverse=True)
        themes = themes[:8]

    risk_msgs = [m for m in user_msgs if _RISK_RE.search(m["text"])]
    risk_events = [{
        "date": m["dt"].strftime("%d %b %Y, %I:%M %p") if m["dt"] else "—",
        "excerpt": (m["text"][:110] + "…") if len(m["text"]) > 110 else m["text"],
    } for m in risk_msgs[-5:]]
    risk_mem = (_row_get(memory, "risk_pattern", "") or "").strip()

    chat = {
        "user_messages": len(user_msgs),
        "total_messages": len(chat_rows),
        "active_days": len(active_days),
        "first": _fmt_date(user_msgs[0]["dt"]) if user_msgs else "—",
        "last": _fmt_date(user_msgs[-1]["dt"]) if user_msgs else "—",
        "avg_words": round(sum(len(m["text"].split()) for m in user_msgs) / len(user_msgs)) if user_msgs else 0,
        "language": ("Mostly Urdu" if urdu_count > len(user_msgs) * 0.6 else
                     "Mostly English / Roman Urdu" if urdu_count < len(user_msgs) * 0.4 else
                     "Urdu and English") if user_msgs else "—",
        "time_of_day": time_buckets.most_common(1)[0][0] if time_buckets else "—",
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
        "dob": _fmt_date(_parse_dt(_row_get(account, "dob"))),
        "age": _age_from_dob(_row_get(account, "dob")),
        "profession": profession,
        "living_place": (living_place or "").strip().title() or "Not specified",
        "displaced": "Yes" if (_row_get(memory, "is_displaced") or displacement) else "Not stated",
        "displacement": displacement or "Not specified",
        "hardships": hardships,
        "stressors": [str(s) for s in stressors][:6],
        "member_since": _fmt_date(created),
        "last_active": _fmt_date(last_seen),
        "extra": extra_fields,
    }

    fingerprint = (
        len(chat_rows), _row_get(chat_rows[-1], "id") if chat_rows else 0,
        len(wellbeing), len(assessments), _row_get(assess_rows[-1], "id") if assess_rows else 0,
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
        "risk_memory": risk_mem,
        "user_texts": [m["text"] for m in user_msgs],
        "fingerprint": fingerprint,
        "now_pk": now_pk,
        "since_pk": since_pk,
    }


# ─────────────────────────────────────────────
# Overall status (rule based, so it is consistent and explainable)
# ─────────────────────────────────────────────
def _overall_status(data):
    latest = data["latest"] or {}
    phq, gad, who = latest.get("phq"), latest.get("gad"), latest.get("who")
    reasons = []
    if data["chat"]["risk_count"]:
        reasons.append(f'{data["chat"]["risk_count"]} message(s) with signs of suicidal thoughts or self-harm in this period')
    if phq is not None and phq >= 20:
        reasons.append(f"PHQ-9 score of {phq} (severe range)")
    if reasons:
        return {"key": "urgent", "label": "Urgent attention recommended", "reasons": reasons}

    if phq is not None and phq >= 15:
        reasons.append(f"PHQ-9 score of {phq} (moderately severe)")
    if gad is not None and gad >= 15:
        reasons.append(f"GAD-7 score of {gad} (severe anxiety range)")
    if who is not None and who <= 7:
        reasons.append(f"WHO-5 score of {who} (very low wellbeing)")
    if reasons:
        return {"key": "attention", "label": "Needs professional attention", "reasons": reasons}

    if phq is not None and phq >= 10:
        reasons.append(f"PHQ-9 score of {phq} (moderate)")
    if gad is not None and gad >= 10:
        reasons.append(f"GAD-7 score of {gad} (moderate)")
    if (data["wb"]["low_mood_share"] or 0) >= 50:
        reasons.append(f'low or anxious mood in {data["wb"]["low_mood_share"]}% of daily entries')
    if data["wb"]["avg_sleep"] is not None and data["wb"]["avg_sleep"] < 5:
        reasons.append(f'average sleep of {data["wb"]["avg_sleep"]:g} hours')
    if reasons:
        return {"key": "monitor", "label": "Monitor closely", "reasons": reasons}

    if not data["latest"] and not data["wellbeing"] and not data["chat"]["user_messages"]:
        return {"key": "nodata", "label": "Not enough data yet", "reasons": ["no assessments, wellbeing entries or conversations in this period"]}
    return {"key": "stable", "label": "Stable", "reasons": ["no high-risk indicators in this period"]}


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
 "progress": "2 sentences comparing earlier and later data in the period",
 "recommendations": ["4-6 concrete, practical next steps for the professional and the client"]
}"""


def _fallback_narrative(data, status):
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


def _ai_narrative(data, status, period_label):
    key = (data["user_id"], period_label, data["fingerprint"])
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
            messages=[{"role": "system", "content": REPORT_PROMPT}, {"role": "user", "content": content}],
            temperature=0.3,
            max_tokens=1100,
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
# Route
# ─────────────────────────────────────────────
@app.route("/report")
@login_required
def wellbeing_report():
    period = request.args.get("period", "30")
    if period not in REPORT_PERIODS:
        period = "30"
    days = REPORT_PERIODS[period]

    data = _collect(session.get("account_id"), days)
    status = _overall_status(data)
    now_pk = data["now_pk"]
    period_label = (f'{_fmt_date(data["since_pk"])} – {_fmt_date(now_pk)} (last {days} days)'
                    if days else f'All records up to {_fmt_date(now_pk)}')
    narrative = _ai_narrative(data, status, period_label)

    report_id = f'EAI-{now_pk.strftime("%Y%m%d")}-{(data["user_id"] or 0):04d}'
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
        printed_on=now_pk.strftime("%d %B %Y, %I:%M %p"),
        report_id=report_id,
        file_name=f'Empath_AI_Report_{safe_name}_{now_pk.strftime("%Y-%m-%d")}',
    )


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .core import client  # noqa: E402
from .database import get_db, get_user_by_account_id  # noqa: E402
from .context import IDP_HARDSHIP_KEYWORDS  # noqa: E402
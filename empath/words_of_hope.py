"""Words of Hope: conversation mood tracking and quotes API.

Split out of the original app.py. The code below is unchanged."""
import os
import re
import csv
import sqlite3
import pickle
import json
import random
import hashlib
import numpy as np
import asyncio
import io
import edge_tts
import hmac

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)
from datetime import datetime, timezone, timedelta

from datetime import datetime, timedelta
from functools import wraps
from sklearn.metrics.pairwise import cosine_similarity

from flask import Flask, request, jsonify, render_template, session, g, redirect, url_for, send_file
from openai import OpenAI
from gnuhealth_connector import (
     create_mygnuhealth_profile,
    update_mygnuhealth_tracker,
)
from quranic_corner_service import get_category_verses
from .core import (
    app,
    login_required,
)

    
    # ============================================================
# WORDS OF HOPE — FIXED MOOD CATEGORY + BILINGUAL QUOTES
# Uses the emotion already detected by the chat route. No AI quote generation.
# ============================================================

@app.route("/quotes")
@login_required
def quotes_page():
    return render_template("quotes.html")


def _words_of_hope_mood(emotion):
    label = str(emotion or "").strip().lower().replace("-", "_").replace(" ", "_")

    aliases = {
        "sad": "sad",
        "sadness": "sad",
        "depressed": "sad",
        "depression": "sad",
        "very_low": "sad",
        "low": "sad",
        "grief": "sad",

        "anxious": "anxious",
        "anxiety": "anxious",
        "fear": "anxious",
        "stress": "anxious",
        "panic": "anxious",
        "worry": "anxious",

        "hope": "hope",
        "hopeful": "hope",
        "inspiration": "hope",
        "neutral": "hope",
        "unknown": "hope",
        "okay": "hope",
        "emotional_support": "hope",

        "positive": "positive",
        "good": "positive",
        "happy": "positive",
        "happiness": "positive",
        "joy": "positive",
        "gratitude": "positive",

        "angry": "angry",
        "anger": "angry",
        "frustration": "angry",
        "irritation": "angry",

        "lonely": "lonely",
        "loneliness": "lonely",
        "isolation": "lonely",

        "tired": "tired",
        "fatigue": "tired",
        "exhausted": "tired",
        "exhaustion": "tired",

        "homesick": "homesick",
        "homesickness": "homesick",
    }

    if label in aliases:
        return aliases[label]

    if any(x in label for x in ("anger", "angry", "frustrat")):
        return "angry"
    if any(x in label for x in ("lonel", "isolat")):
        return "lonely"
    if any(x in label for x in ("tired", "fatigue", "exhaust")):
        return "tired"
    if any(x in label for x in ("sad", "depress", "grief")):
        return "sad"
    if any(x in label for x in ("anx", "fear", "stress", "panic", "worr")):
        return "anxious"
    if any(x in label for x in ("posit", "happy", "joy", "grat")):
        return "positive"
    if any(x in label for x in ("hope", "inspir")):
        return "hope"

    return "hope"



# Clear emotional expressions in English, Urdu and Roman Urdu.
# Checked in WOH_MOOD_ORDER: more specific feelings (angry, lonely, homesick, tired)
# are checked before the general ones (anxious, sad).
WOH_MOOD_PATTERNS = {
    "angry": [
        "angry", "frustrated", "furious", "so mad", "irritated", "want to break",
        "غصہ", "غصے", "چڑچڑا", "توڑ دوں",
        "ghussa", "gussa", "ghussay", "chirchira", "tor dun", "tod dun"
    ],

    "anxious": [
        "worried", "overwhelmed", "scared", "nervous",
        "panic", "under pressure", "anxious", "anxiety",
        "heart starts beating fast", "can't calm down", "cannot calm down",
        "پریشان", "گھبراہٹ", "بے چین",
        "فکر مند", "دباؤ میں", "ڈر لگ",
        "pareshan", "preshan", "ghabrahat", "bechain", "dar lagta", "tension"
    ],

    "lonely": [
        "lonely", "alone", "nobody cares", "no one cares", "no one listens",
        "nobody listens", "never listen", "ignoring us", "ignoring me", "no friends",
        "تنہا", "اکیلا", "اکیلی", "اکیلے پن", "کسی کو پرواہ نہیں", "نظرانداز",
        "akela", "akeli", "tanha", "kisi ko parwah nahi", "koi parwah nahi"
    ],

    "homesick": [
        "miss my home", "miss home", "my old home", "my village", "miss my village",
        "want to go home", "go back home", "my house", "old life",
        "گھر یاد", "گاؤں یاد", "اپنا گھر", "واپس گھر",
        "ghar yaad", "gaon yaad", "apna ghar", "wapas ghar",
        "yaad aata", "yaad aati", "yaad ata", "yaad ati",
        "یاد آتا", "یاد آتی"
    ],

    "tired": [
        "tired", "exhausted", "drained", "no energy", "worn out", "can't sleep",
        "cannot sleep", "so sleepy", "burnt out", "burned out",
        "تھک", "تھکن", "تھکاوٹ", "نیند نہیں",
        "thak gaya", "thak gayi", "thakan", "thakawat", "neend nahi"
    ],

    "sad": [
        "lost", "empty", "heartbroken",
        "helpless", "broken", "confused",
        "don't know what to do", "do not know what to do",
        "feel like crying", "very sad", "depressed", "sad",
        "no point", "miss my",
        "اداس", "دل ٹوٹ گیا", "خالی پن", "بے بس",
        "سمجھ نہیں آ رہا", "کیا کروں",
        "دل نہیں لگ رہا", "مایوس", "غمگین", "کمی محسوس",
        "udaas", "udas", "dukhi", "bura din", "bura guzra",
        "dil nahi lag raha", "mayoos"
    ],

    "positive": [
        "grateful", "thankful", "blessed",
        "happy", "joyful", "feeling good",
        "thank you", "thanks", "made my day", "good news",
        "passed my", "proud", "excited", "loved it", "everyone loved",
        "congrat", "alhamdulillah",
        "شکر گزار", "خوش", "خوش نصیب",
        "اللہ کا شکر", "خوشی محسوس", "شکریہ", "مبارک", "الحمدللہ",
        "khush", "shukriya", "shukria", "mubarak", "alhamdulillah", "mashallah"
    ],

    "hope": [
        "hopeful", "optimistic", "motivated",
        "inspired", "things will get better",
        "feel a little better", "feeling better", "feel better",
        "getting better", "i will try", "will be okay", "will be fine",
        "پرامید", "امید ہے", "حوصلہ",
        "بہتر ہونے کی امید", "ہمت ہے", "بہتر محسوس",
        "umeed hai", "behtar mehsoos", "behtar lag raha", "himmat hai", "inshallah"
    ]
}

WOH_MOOD_ORDER = ["angry", "anxious", "lonely", "homesick", "tired", "sad", "positive", "hope"]
WOH_ALL_MOODS = set(WOH_MOOD_ORDER)

# If quotes.json has no quotes yet for a new mood, show its closest older mood
WOH_PARENT_MOOD = {
    "angry": "anxious",
    "lonely": "sad",
    "homesick": "sad",
    "tired": "hope",
}

WOH_CATEGORIES = {
    "sad": {"en": "Comfort", "ur": "تسلی"},
    "anxious": {"en": "Peace", "ur": "سکون"},
    "hope": {"en": "Inspiration", "ur": "حوصلہ افزائی"},
    "positive": {"en": "Gratitude", "ur": "شکرگزاری"},
    "angry": {"en": "Patience", "ur": "صبر"},
    "lonely": {"en": "Belonging", "ur": "ساتھ"},
    "tired": {"en": "Strength", "ur": "ہمت"},
    "homesick": {"en": "Home", "ur": "گھر"},
}

WOH_NEGATED_POSITIVE = [
    "not happy", "not feeling good", "not good", "not okay", "not fine",
    "not better", "no longer happy",
    "خوش نہیں", "اچھا محسوس نہیں",
    "khush nahi", "acha nahi lag raha", "theek nahi",
]


def _explicit_words_of_hope_mood(user_message):
    """Return a mood only when the message clearly expresses one, else None."""
    message = str(user_message or "").lower().strip()
    if not message:
        return None

    # "I am not happy" should never count as positive
    if any(p in message for p in WOH_NEGATED_POSITIVE):
        return "sad"

    for mood in WOH_MOOD_ORDER:
        if any(phrase in message for phrase in WOH_MOOD_PATTERNS[mood]):
            return mood
    return None


def save_words_of_hope_mood(user_id, mood):
    """Persist the latest conversation mood so the page works after reload/re-login."""
    if not user_id or not mood:
        return
    try:
        ensure_user_memory(user_id)
        db = get_db()
        db.execute(
            "UPDATE user_memory SET words_of_hope_mood = ? WHERE user_id = ?",
            (mood, user_id)
        )
        db.commit()
    except Exception as e:
        print("Words of Hope mood save error:", e)


def get_saved_words_of_hope_mood(user_id):
    if not user_id:
        return ""
    try:
        db = get_db()
        row = db.execute(
            "SELECT words_of_hope_mood FROM user_memory WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        return (row["words_of_hope_mood"] or "") if row else ""
    except Exception as e:
        print("Words of Hope mood read error:", e)
        return ""


# How the Words of Hope mood is built from the WHOLE conversation:
WOH_DECAY = 0.85          # each new message keeps 85% of older scores (old talk still counts)
WOH_EXPLICIT_WEIGHT = 1.0  # a clearly expressed feeling ("I'm so sad", "shukriya")
WOH_GUESS_WEIGHT = 0.3     # a guessed feeling (ML model / vague message like "ok")


def update_words_of_hope_mood(user_message, wellbeing_info=None, emotion=None, force_mood=None):
    """Track the mood of the WHOLE conversation, not just the last message.

    Every message adds to a running score per mood. Older messages fade only
    slowly (85% each turn), so a long sad conversation that ends with one
    "thank you" still shows Comfort quotes, while a conversation that has
    clearly turned happy over several messages moves to Gratitude.
    Clear feelings count much more than guessed ones, so vague messages
    like "ok" barely change anything.
    """
    # force_mood is used for crisis messages, which skip the normal chat flow
    explicit = force_mood or _explicit_words_of_hope_mood(user_message)
    current = explicit or _detect_words_of_hope_mood(user_message, wellbeing_info, emotion)

    old_scores = dict(session.get("woh_mood_scores", {}) or {})
    scores = {
        mood: round(score * WOH_DECAY, 3)
        for mood, score in old_scores.items()
        if score * WOH_DECAY >= 0.05
    }
    scores[current] = scores.get(current, 0) + (
        WOH_EXPLICIT_WEIGHT if explicit else WOH_GUESS_WEIGHT
    )

    mood = max(scores, key=scores.get)

    session["woh_mood_scores"] = scores
    session["words_of_hope_mood"] = mood
    session.modified = True

    save_words_of_hope_mood(session.get("user_id"), mood)
    print("WOH MOOD SCORES:", scores, "->", mood)
    return mood


def _detect_words_of_hope_mood(user_message, wellbeing_info=None, emotion=None):
    # Detect clear mood from the current message
    explicit = _explicit_words_of_hope_mood(user_message)
    if explicit:
        return explicit

    # Use existing wellbeing mood if no clear phrase was found
    if isinstance(wellbeing_info, dict):
        wellbeing_mood = wellbeing_info.get("mood")
        if wellbeing_mood:
            return _words_of_hope_mood(wellbeing_mood)
        if wellbeing_info.get("energy") in ("very_low", "low"):
            return "tired"

    # Use existing ML emotion as fallback
    return _words_of_hope_mood(emotion)

# Quotes from data/quotes.json that are safe to show when someone is in crisis.
# Reviewed one by one: they speak of mercy, belonging and rising again,
# never of death, sin, punishment, tests or "hardship makes you strong".
CRISIS_SAFE_QUOTE_IDS = [
    "hope_hadith_001",      # Allah's mercy divided into 100 parts
    "hope_hadith_002",      # "I am with him when he remembers Me"
    "homesick_hadith_003",  # "You are the Companion on the journey"
    "lonely_hadith_001",    # believers are like one body
    "lonely_hadith_002",    # like a building whose parts support one another
    "sad_hadith_002",       # the plant bends in the wind and rises again
    "lonely_001",           # Ubuntu: a person is a person through other people
    "sad_004",              # Helen Keller: the world is full of the overcoming of suffering
    "hope_010",             # Iqbal: there are still more skies before you
]
CRISIS_CATEGORY = {"en": "You are not alone", "ur": "آپ اکیلے نہیں"}
CRISIS_UNSAFE_WORDS = [
    "death", "die", "dying", "dead", "kill", "grave", "sin", "punish", "hell", "fear",
    "test", "trial", "break", "fight", "sacrifice", "blood", "war", "enemy", "repent",
]


def _crisis_safe_quotes(language):
    try:
        with open(os.path.join(DATA_PATH, "quotes.json"), "r", encoding="utf-8") as f:
            all_quotes = json.load(f)
        all_quotes = [q for q in all_quotes if isinstance(q, dict)
                      and q.get("translations", {}).get("en", {}).get("quote")]
        by_id = {str(q.get("id", "")).strip(): q for q in all_quotes}
        pool = [by_id[i] for i in CRISIS_SAFE_QUOTE_IDS if i in by_id]

        # Backup: if some of the hand-picked ids are missing from this quotes.json,
        # take gentle hope / belonging quotes that avoid risky words.
        if len(pool) < 3:
            for q in all_quotes:
                if q in pool or not set(q.get("moods", [])) & {"hope", "lonely", "homesick"}:
                    continue
                text = " ".join(
                    str(q.get("translations", {}).get("en", {}).get(k, "")) for k in ("quote", "title")
                ).lower()
                if any(w in text for w in CRISIS_UNSAFE_WORDS):
                    continue
                pool.append(q)
        print(f"WORDS OF HOPE (crisis-safe): {len(pool)} quotes available")

        hadiths = [q for q in pool if str(q.get("type", "")).lower() == "hadith"]
        others = [q for q in pool if str(q.get("type", "")).lower() != "hadith"]
        selected = []
        if hadiths:
            selected.append(random.choice(hadiths))
        selected += random.sample(others, min(3 - len(selected), len(others)))
        if len(selected) < 3:
            rest = [q for q in hadiths if q not in selected]
            selected += random.sample(rest, min(3 - len(selected), len(rest)))

        category = CRISIS_CATEGORY[language]
        output = []
        for item in selected:
            tr = item.get("translations", {}).get(language) or item.get("translations", {}).get("en", {})
            output.append({
                "id": item.get("id", ""),
                "quote": tr.get("quote", ""),
                "person": tr.get("person", ""),
                "title": tr.get("title") or tr.get("source", ""),
                "category": category,
                "type": str(item.get("type", "international")).lower(),
                "source_url": item.get("source_url", ""),
                "translation_note": tr.get("translation_note", ""),
            })
        return jsonify({"quotes": output, "language": language, "mood": "hope",
                        "category": category, "status": "ok", "crisis_support": True})
    except Exception:
        app.logger.exception("Words of Hope crisis quotes error")
        return jsonify({"quotes": [], "language": language, "mood": "support", "status": "safety_hold"})


@app.route("/api/quotes", methods=["GET"])
@login_required
def get_quotes():
    language = str(request.args.get("language") or session.get("language") or "en").strip().lower()
    language = "ur" if language in ("ur", "urdu") else "en"

    # During a crisis, show only hand-picked gentle quotes (connection, mercy,
    # "you are not alone"). Nothing about death, sin, testing or "be strong".
    if session.get("crisis_active") or session.get("crisis_level") in ("crisis", "emergency"):
        return _crisis_safe_quotes(language)

    # 1) mood from the current chat session
    # 2) mood saved in the DB from the last chat (the /app page clears the session)
    # 3) ML emotion fallback
    mood = session.get("words_of_hope_mood")

    if not mood:
        woh_user_id = session.get("user_id")
        if not woh_user_id and session.get("account_id"):
            linked = get_user_by_account_id(session.get("account_id"))
            woh_user_id = linked["id"] if linked else None
        mood = get_saved_words_of_hope_mood(woh_user_id)

    if mood not in WOH_ALL_MOODS:
        mood = _words_of_hope_mood(session.get("emotion", "unknown"))
    print("WORDS OF HOPE DEBUG:")
    print("SESSION EMOTION:", session.get("emotion"))
    print("MAPPED MOOD:", mood)
    fixed_categories = WOH_CATEGORIES
    try:
        quotes_path = os.path.join(DATA_PATH, "quotes.json")
        with open(quotes_path, "r", encoding="utf-8") as f:
            all_quotes = json.load(f)
        if not isinstance(all_quotes, list):
            raise ValueError("data/quotes.json must contain a JSON list")

        candidates = [q for q in all_quotes if isinstance(q, dict)
                      and mood in q.get("moods", [])
                      and q.get("translations", {}).get("en", {}).get("quote")]

        # New mood with no quotes in the JSON yet -> use its closest older mood,
        # but keep showing the new category name (Patience, Belonging...).
        if not candidates and mood in WOH_PARENT_MOOD:
            parent = WOH_PARENT_MOOD[mood]
            candidates = [q for q in all_quotes if isinstance(q, dict)
                          and parent in q.get("moods", [])
                          and q.get("translations", {}).get("en", {}).get("quote")]

        seen_by_mood = session.get("words_of_hope_seen", {})
        seen = set(seen_by_mood.get(mood, []))
        unseen = [q for q in candidates if q.get("id") not in seen]
        if not unseen and candidates:
            seen = set()
            unseen = candidates[:]

        # Separate Hadith and normal quotations
        hadiths = [
            q for q in unseen
            if str(q.get("type", "")).strip().lower() == "hadith"
        ]

        normal_quotes = [
            q for q in unseen
            if str(q.get("type", "")).strip().lower() != "hadith"
        ]

        selected = []

        # Include one Hadith if available
        if hadiths:
            selected.append(random.choice(hadiths))

        # Fill remaining slots with normal quotations
        remaining_slots = 3 - len(selected)

        if remaining_slots > 0 and normal_quotes:
            selected.extend(
                random.sample(
                    normal_quotes,
                    min(remaining_slots, len(normal_quotes))
                )
            )

        # Fill remaining slots with Hadith if needed
        if len(selected) < 3:
            remaining_hadiths = [
                q for q in hadiths if q not in selected
            ]

            selected.extend(
                random.sample(
                    remaining_hadiths,
                    min(3 - len(selected), len(remaining_hadiths))
                )
            )

        seen.update(q["id"] for q in selected if q.get("id"))
        seen_by_mood[mood] = list(seen)
        session["words_of_hope_seen"] = seen_by_mood
        session.modified = True

        output = []
        for item in selected:
            tr = item.get("translations", {}).get(language) or item.get("translations", {}).get("en", {})
            kind = str(item.get("type", "international")).lower()
            output.append({
                "id": item.get("id", ""),
                "quote": tr.get("quote", ""),
                "person": tr.get("person", ""),
                "title": tr.get("title") or tr.get("source", ""),
                "category": fixed_categories[mood][language],
                "type": kind,
                "source_url": item.get("source_url", ""),
                "translation_note": tr.get("translation_note", ""),
            })
        return jsonify({"quotes": output, "language": language, "mood": mood,
                        "category": fixed_categories[mood][language], "status": "ok"})
    except Exception:
        app.logger.exception("Words of Hope API error")
        return jsonify({"quotes": [], "language": language, "mood": mood,
                        "status": "error"}), 500


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .core import (  # noqa: E402
    DATA_PATH,
    f,
)
from .database import (  # noqa: E402
    ensure_user_memory,
    get_db,
    get_user_by_account_id,
)
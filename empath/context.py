"""IDP context, wellbeing extraction (sleep/mood/energy), survey insights, session helpers.

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
    BASE_PATH,
    f,
)



# ─────────────────────────────────────────────
# IDP CONTEXT
# ─────────────────────────────────────────────
DISPLACEMENT_KEYWORDS = {
    "flood": [
        "flood", "flooding", "flooded", "selaab", "barish", "baarish",
        "water came", "water level", "river", "dam broke", "washed away",
        "flood victims", "flood affected", "toofan"
    ],
    "conflict": [
        "conflict", "violence", "attack", "firing", "shooting", "bomb", "war",
        "military", "operation", "terrorists", "militants", "forced to leave",
        "had to escape", "fled", "running away", "danger", "unsafe", "threats"
    ],
    "earthquake": [
        "earthquake", "quake", "tremor", "bhookamp", "zalzala", "shaking",
        "building collapsed", "house fell", "rubble"
    ],
    "eviction": [
        "evicted", "kicked out", "landlord", "removed from house",
        "house demolished", "lost our place", "had nowhere to go"
    ],
    "fire": [
        "fire", "burned", "burnt", "our house burned", "caught fire", "aag"
    ],
}

def detect_displacement_cause(conversation_history):
    full_text = " ".join([
        item.get("content", "").lower()
        for item in conversation_history
        if item.get("role") == "user"
    ])

    scores = {cause: 0 for cause in DISPLACEMENT_KEYWORDS}
    for cause, keywords in DISPLACEMENT_KEYWORDS.items():
        for kw in keywords:
            if kw in full_text:
                scores[cause] += 1

    best_cause = max(scores, key=scores.get)
    if scores[best_cause] > 0:
        return best_cause
    return "displacement"

def get_displacement_phrase(cause):
    phrases = {
        "flood": "after the floods forced your family to leave home",
        "conflict": "after being displaced due to violence and insecurity",
        "earthquake": "after the earthquake took away your home",
        "eviction": "after losing your home so suddenly",
        "fire": "after losing your home to fire",
        "displacement": "after being displaced from your home",
    }
    return phrases.get(cause, "after being displaced from your home")

IDP_HARDSHIP_KEYWORDS = {
    "loss_of_home": [
        "miss my home", "lost my home", "left home", "ghar chorna para",
        "ghar chor diya", "our house is gone", "apna ghar yaad aata hai",
        "my room", "my bed", "my house", "my village", "my area"
    ],
    "camp_stress": [
        "camp", "shelter", "tent", "ngo", "shared place", "crowded",
        "no privacy", "privacy nahi", "room mate", "host family", "temporary place"
    ],
    "family_burden": [
        "children", "kids", "bachay", "bacha", "family", "husband", "wife",
        "mother", "father", "parents", "responsibility", "ghar walay"
    ],
    "livelihood_loss": [
        "no job", "lost job", "jobless", "rozgar", "kamai", "income",
        "earn", "earning", "daily wage", "not working", "without work"
    ],
    "education_disruption": [
        "school", "college", "university", "study", "studies", "exam",
        "class", "semester", "fees", "education"
    ],
    "safety_fear": [
        "unsafe", "fear", "scared", "dar", "threat", "violence", "afraid",
        "not safe", "worried for safety"
    ],
    "grief_memory": [
        "old life", "used to live", "miss those days", "miss my village",
        "miss my room", "miss my area", "miss my things", "my home comfort"
    ],
}

IDP_SUPPORT_LINES = {
    "loss_of_home": [
        "Losing the comfort of home can leave a person feeling emotionally ungrounded.",
        "When home is no longer there, even small daily things can start hurting deeply.",
        "Missing your own room, routine, and familiar space can weigh heavily on the heart."
    ],
    "camp_stress": [
        "Living in a shared or temporary place can become mentally exhausting over time.",
        "When there is little privacy or rest, both the heart and mind can feel tired.",
        "Crowded living and constant adjustment can slowly drain a person emotionally."
    ],
    "family_burden": [
        "Trying to stay strong for family while carrying your own pain can be exhausting.",
        "Caring for others during displacement often leaves very little room for your own emotions.",
        "Many people carry family responsibilities quietly, even when they are already overwhelmed."
    ],
    "livelihood_loss": [
        "Losing work after displacement can bring both stress and emotional heaviness.",
        "Not knowing how to manage expenses can put a lot of pressure on a person.",
        "When income becomes uncertain, worry can stay in the mind all day."
    ],
    "education_disruption": [
        "When studies and routine are disrupted, even simple tasks can start feeling harder.",
        "Uncertainty about education and the future can make a person feel stuck and worried.",
        "Losing focus after so much change is a very human response."
    ],
    "safety_fear": [
        "When safety has been shaken, the body and mind can stay tense for a long time.",
        "After frightening experiences, it is common to feel alert, uneasy, or unsettled.",
        "Feeling unsafe can make rest, sleep, and peace very difficult."
    ],
    "grief_memory": [
        "Sometimes it is not only the house people miss, but the life they had inside it.",
        "Missing your old routine, your people, and your space can feel like a deep kind of grief.",
        "Memories of home can return strongly when life feels uncertain."
    ],
}

ROLE_CONTEXT_LINES = {
    "student": [
        "When studies, routine, and future plans are interrupted, the mind can feel pulled in many directions.",
        "For students, displacement can make focus, motivation, and hope for the future feel shaky."
    ],
    "homemaker": [
        "When someone is carrying home responsibilities in unstable conditions, the emotional burden can become very heavy.",
        "Trying to manage family needs away from home can leave a homemaker feeling worn out inside."
    ],
    "parent": [
        "Parents often carry double stress, their own pain and the pressure to stay strong for children.",
        "Worrying about children during displacement can keep the heart constantly heavy."
    ],
    "unemployed": [
        "When displacement also affects work and purpose, it can bring helplessness and emotional pain.",
        "Losing stability in both home and livelihood can make a person feel deeply stuck."
    ],
    "daily_worker": [
        "For someone who depends on daily earnings, uncertainty after displacement can feel overwhelming.",
        "When survival and income are both uncertain, stress can stay present from morning to night."
    ],
    "elderly": [
        "For older people, losing familiar surroundings can bring loneliness, fear, and emotional heaviness.",
        "Changes in place, routine, and support can affect elderly people very deeply."
    ],
    "government": [
        "Even when a person is trying to keep functioning, displacement can quietly build stress inside.",
        "Balancing responsibilities while carrying personal loss can become emotionally exhausting."
    ],
    "default": [
        "Displacement can affect a person's peace, routine, and emotional balance in many quiet ways.",
        "When life changes so suddenly, it is natural for the heart and mind to feel burdened."
    ],
}

def detect_idp_hardships(conversation_history):
    full_text = " ".join([
        item.get("content", "").lower()
        for item in conversation_history
        if item.get("role") == "user"
    ])

    scores = {}
    for hardship, keywords in IDP_HARDSHIP_KEYWORDS.items():
        score = 0
        for kw in keywords:
            if kw in full_text:
                score += 1
        scores[hardship] = score

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return [item[0] for item in ranked if item[1] > 0][:2]

def get_idp_context_line(role, hardships):
    lines = []

    role_lines = ROLE_CONTEXT_LINES.get(role, ROLE_CONTEXT_LINES["default"])
    if role_lines:
        lines.append(random.choice(role_lines))

    for hardship in hardships[:2]:
        if hardship in IDP_SUPPORT_LINES:
            lines.append(random.choice(IDP_SUPPORT_LINES[hardship]))

    if not lines:
        lines.append(random.choice(ROLE_CONTEXT_LINES["default"]))

    return " ".join(lines[:2])

# ─────────────────────────────────────────────
# WELLBEING EXTRACTION: SLEEP / MOOD / ENERGY
# ─────────────────────────────────────────────

MOOD_KEYWORDS = {
    "very_low": [
        "very sad", "depressed", "hopeless", "broken", "empty",
        "i want to cry", "i feel dead", "nothing matters",
        "بہت اداس ہوں", "بہت زیادہ اداس", "بہت مایوس ہوں",
        "دل بہت اداس ہے", "بہت زیادہ پریشان ہوں"
    ],
    "low": [
        "sad", "down", "not okay", "not fine", "upset",
        "feeling bad", "low mood", "mood is low",
        "my mood is low", "mood is bad", "my mood is bad",
        "not a good day", "bad day", "my day was bad",
        "a difficult day", "rough day",
        "آج میرا دن اچھا نہیں گزرا",
        "میرا دن اچھا نہیں گزرا",
        "آج کا دن اچھا نہیں تھا",
        "آج دن اچھا نہیں گزرا",
        "آج اچھا محسوس نہیں کر رہا",
        "آج اچھا محسوس نہیں کر رہی",
        "دل اداس ہے", "میں اداس ہوں", "اداس ہوں",
        "پریشان ہوں", "طبیعت اچھی نہیں",
        "دل نہیں لگ رہا", "موڈ خراب ہے",
        "میرا موڈ خراب ہے", "اچھا محسوس نہیں ہو رہا",
        "اچھا محسوس نہیں ہو رہی", "برا دن تھا",
        "دن برا گزرا", "دل پریشان ہے"
    ],
    "anxious": [
        "anxious", "worried", "scared", "fear", "panic",
        "ghabrahat", "bechain",
        "گھبراہٹ", "بے چین ہوں", "بے چینی",
        "فکر ہو رہی ہے", "بہت فکر ہے", "ڈر لگ رہا ہے",
        "خوف محسوس ہو رہا ہے", "پریشانی ہو رہی ہے"
    ],
    "okay": [
        "okay", "fine", "normal", "manageable",
        "ٹھیک ہوں", "میں ٹھیک ہوں", "معمول کے مطابق"
    ],
    "good": [
        "better", "good", "calm", "relaxed", "peaceful",
        "اچھا محسوس کر رہا ہوں", "اچھا محسوس کر رہی ہوں",
        "آج دن اچھا تھا", "میرا دن اچھا گزرا",
        "بہتر محسوس کر رہا ہوں", "بہتر محسوس کر رہی ہوں",
        "خوش ہوں", "پرسکون ہوں"
    ]
}


ENERGY_KEYWORDS = {
    "very_low": [
        "no energy", "completely tired", "dead tired",
        "exhausted", "drained", "can't move",
        "بالکل توانائی نہیں", "بہت زیادہ تھکا ہوا",
        "بہت زیادہ تھکی ہوئی", "انتہائی تھکا ہوا",
        "انتہائی تھکی ہوئی", "بالکل نڈھال"
    ],

    "low": [
        "tired", "weak", "fatigue", "low energy",
        "thaka hua", "thaki hui", "energy is low",
        "my energy is low", "energy is bad", "my energy is bad",
        "اتنی تھکن ہوتی ہے", "کچھ کرنے کا دل نہیں کرتا",
        "میں بہت تھکی ہوئی ہوں", "میں بہت تھکا ہوا ہوں",
        "ذہنی تھکن ہے", "جسم میں کمزوری محسوس ہو رہی ہے",
        "جسمانی تھکن محسوس ہو رہی ہے", "کسی کام میں دل نہیں لگتا",
        "انرجی کم ہے", "میری انرجی کم ہے",
        "توانائی کم ہے", "میری توانائی کم ہے"
    ],

    "medium": [
        "normal", "normal energy", "energy is normal",
        "my energy is normal", "energy level is normal",
        "energy is okay", "energy is fine",
        "انرجی ٹھیک ہے", "انرجی بھی ٹھیک ہے",
        "میری انرجی ٹھیک ہے", "توانائی معمول کے مطابق ہے",
        "میری توانائی ٹھیک ہے", "انرجی نارمل ہے"
    ],

    "high": [
        "energetic", "active", "full energy", "motivated",
        "energy is good", "my energy is good",
        "energy level is good", "energy level is high",
        "انرجی اچھی ہے", "میری انرجی اچھی ہے",
        "توانائی اچھی ہے", "میری توانائی اچھی ہے",
        "پُرجوش ہوں", "بہت چاق و چوبند ہوں"
    ]
}



SLEEP_KEYWORDS = [
    "sleep", "slept", "sleeping", "bed", "insomnia",
    "night", "awake", "rest", "neend", "nend",
    "نیند", "سویا", "سوئی", "سونا", "رات کو جاگتا",
    "رات کو جاگتی", "نیند نہیں آئی", "نیند نہیں آتی",
    "سو نہیں سکا", "سو نہیں سکی", "نیند پوری نہیں ہوئی"
]


def extract_sleep_hours(text):
    t = text.lower()

    # English numeric sleep statements
    patterns = [
        r"(\d+(?:\.\d+)?)\s*(?:hours|hour|hrs|hr)\s*(?:of sleep|sleep)?",
        r"slept\s*(?:for\s*)?(\d+(?:\.\d+)?)\s*(?:hours|hour|hrs|hr)?",
        r"sleep(?:ing)?\s*(?:for\s*)?(\d+(?:\.\d+)?)\s*(?:hours|hour|hrs|hr)?",
        r"only slept\s*(\d+(?:\.\d+)?)",
        r"i slept\s*(\d+(?:\.\d+)?)",
        r"i sleep only\s*(\d+(?:\.\d+)?)\s*(?:hours|hour|hrs|hr)?",
        r"just\s*(\d+(?:\.\d+)?)\s*(?:hours|hour|hrs|hr)"
    ]

    for pattern in patterns:
        match = re.search(pattern, t)
        if match:
            try:
                hours = float(match.group(1))
                if 0 <= hours <= 24:
                    return hours
            except (ValueError, IndexError):
                pass

    # English number words
    word_number_map = {
        "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "eleven": 11, "twelve": 12
    }

    for word, num in word_number_map.items():
        if (
            re.search(rf"(slept|sleep|sleeping).{{0,10}}\b{word}\b", t)
            or re.search(rf"\b{word}\b.{{0,10}}(hours|hour|hrs|hr)", t)
        ):
            return float(num)

    # Urdu numerals: ۰۱۲۳۴۵۶۷۸۹
    urdu_digits = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
    normalized = text.translate(urdu_digits)

    # Example: میں نے ۵ گھنٹے نیند لی
    urdu_patterns = [
        r"(\d+(?:\.\d+)?)\s*(?:گھنٹے|گھنٹہ|گھنٹوں)",
        r"(?:نیند|سویا|سوئی).{0,20}?(\d+(?:\.\d+)?)\s*(?:گھنٹے|گھنٹہ|گھنٹوں)",
    ]

    for pattern in urdu_patterns:
        match = re.search(pattern, normalized)
        if match:
            try:
                hours = float(match.group(1))
                if 0 <= hours <= 24:
                    return hours
            except (ValueError, IndexError):
                pass

    # Urdu number words, only when sleep is mentioned
    urdu_word_numbers = {
        "ایک": 1, "دو": 2, "تین": 3, "چار": 4,
        "پانچ": 5, "چھ": 6, "سات": 7, "آٹھ": 8,
        "نو": 9, "دس": 10, "گیارہ": 11, "بارہ": 12
    }

    if any(word in normalized for word in SLEEP_KEYWORDS):
        for word, num in urdu_word_numbers.items():
            if word in normalized and any(
                unit in normalized for unit in ["گھنٹے", "گھنٹہ", "گھنٹوں"]
            ):
                return float(num)

    return None


def extract_mood(text):
    t = text.lower()

    # Check stronger moods first
    for label in ["very_low", "anxious", "low", "good", "okay"]:
        for keyword in MOOD_KEYWORDS[label]:
            if keyword in t:
                return label

    return None


def extract_energy(text):
    t = text.lower()

    for label in ["very_low", "low", "medium", "high"]:
        for keyword in ENERGY_KEYWORDS[label]:
            if keyword in t:
                return label

    return None


def extract_wellbeing_info(text):
    sleep_hours = extract_sleep_hours(text)
    mood = extract_mood(text)
    energy = extract_energy(text)

    text_l = text.lower()

    mentioned_sleep = (
        sleep_hours is not None
        or any(word in text_l for word in SLEEP_KEYWORDS)
    )

    mentioned_mood = (
        mood is not None
        or any(word in text_l for word in [
            "mood", "sad", "down", "depressed", "okay", "fine",
            "hopeless", "anxious", "worst", "not feeling well",
            "not good", "دن", "اداس", "پریشان", "خوش",
            "موڈ", "مایوس", "گھبراہٹ", "پرسکون"
        ])
    )

    mentioned_energy = (
        energy is not None
        or any(word in text_l for word in [
            "energy", "tired", "exhausted", "drained", "fatigue",
            "weak", "توانائی", "تھکا", "تھکی", "تھکن",
            "کمزوری", "نڈھال"
        ])
    )

    return {
        "sleep_hours": sleep_hours if mentioned_sleep else None,
        "mood": mood if mentioned_mood else None,
        "energy": energy if mentioned_energy else None,
        "mentioned_sleep": mentioned_sleep,
        "mentioned_mood": mentioned_mood,
        "mentioned_energy": mentioned_energy,
        "has_any": any([
            mentioned_sleep,
            mentioned_mood,
            mentioned_energy
        ])
    }


def save_wellbeing_if_mentioned(user_id, user_message):
    if not user_id:
        return None

    extracted = extract_wellbeing_info(user_message)

    if not extracted["has_any"]:
        return None

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT * FROM wellbeing_logs
        WHERE user_id = ?
        AND date(log_date) = date('now')
        ORDER BY id DESC
        LIMIT 1
    """, (user_id,))

    today_log = cursor.fetchone()

    if today_log:
        sleep = (
            extracted["sleep_hours"]
            if extracted["sleep_hours"] is not None
            else today_log["sleep_hours"]
        )
        mood = (
            extracted["mood"]
            if extracted["mood"] is not None
            else today_log["mood"]
        )
        energy = (
            extracted["energy"]
            if extracted["energy"] is not None
            else today_log["energy"]
        )

        new_source = user_message
        old_source = today_log["source_text"] or ""

        if user_message.strip().lower() not in old_source.lower():
            new_source = f"{old_source} | {user_message}" if old_source else user_message

        cursor.execute("""
            UPDATE wellbeing_logs
            SET sleep_hours = ?, mood = ?, energy = ?, source_text = ?
            WHERE id = ?
        """, (
            sleep, mood, energy, new_source, today_log["id"]
        ))

    else:
        cursor.execute("""
            INSERT INTO wellbeing_logs
                (user_id, sleep_hours, mood, energy, source_text)
            VALUES (?, ?, ?, ?, ?)
        """, (
            user_id,
            extracted["sleep_hours"],
            extracted["mood"],
            extracted["energy"],
            user_message
        ))

    db.commit()
    return extracted

# ─────────────────────────────────────────────
# END WELLBEING EXTRACTION
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# SURVEY INSIGHTS
# ─────────────────────────────────────────────
def _parse_survey_score(val):
    m = re.match(r"^(\d+):", str(val).strip())
    return int(m.group(1)) if m else None

def load_idp_survey_insights():
    survey_path = os.path.join(BASE_PATH, "survey.csv")
    if not os.path.exists(survey_path):
        return {}

    PHQ_LABELS = [
        "low interest", "hopelessness", "sleep trouble", "low energy",
        "appetite issues", "self-failure feelings", "poor concentration",
        "psychomotor changes", "thoughts of self-harm"
    ]
    GAD_LABELS = [
        "nervousness", "uncontrollable worry", "excessive worry",
        "trouble relaxing", "restlessness", "irritability", "fear"
    ]
    PROF_MAP = {
        "student": "student",
        "unemployed": "unemployed",
        "daily wage": "daily_worker",
        "government": "government",
        "private employee": "government",
        "small business": "government",
        "shopkeeper": "government",
        "homemaker": "homemaker",
        "housewife": "homemaker",
        "developer": "government",
        "parent": "parent",
        "mother": "parent",
        "father": "parent",
    }

    groups = {}
    with open(survey_path, encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        phq_indices = list(range(3, 12))
        gad_indices = list(range(12, 19))

        for row in reader:
            if len(row) < 19:
                continue

            prof_raw = row[2].strip().lower()
            role = "default"
            for key, mapped in PROF_MAP.items():
                if key in prof_raw:
                    role = mapped
                    break

            if role not in groups:
                groups[role] = {"phq": [[] for _ in PHQ_LABELS], "gad": [[] for _ in GAD_LABELS], "count": 0}

            groups[role]["count"] += 1

            for i, col in enumerate(phq_indices):
                s = _parse_survey_score(row[col])
                if s is not None:
                    groups[role]["phq"][i].append(s)

            for i, col in enumerate(gad_indices):
                s = _parse_survey_score(row[col])
                if s is not None:
                    groups[role]["gad"][i].append(s)

    insights = {}
    for role, data in groups.items():
        phq_means = {PHQ_LABELS[i]: round(sum(v) / len(v), 2) for i, v in enumerate(data["phq"]) if v}
        gad_means = {GAD_LABELS[i]: round(sum(v) / len(v), 2) for i, v in enumerate(data["gad"]) if v}
        insights[role] = {
            "count": data["count"],
            "top_phq": sorted(phq_means, key=phq_means.get, reverse=True)[:3],
            "top_gad": sorted(gad_means, key=gad_means.get, reverse=True)[:3],
        }
    return insights

IDP_SURVEY_INSIGHTS = load_idp_survey_insights()
print(f"IDP survey insights: {list(IDP_SURVEY_INSIGHTS.keys())}")

# ─────────────────────────────────────────────
# STAGES
# ─────────────────────────────────────────────
STAGE_NAME = "name"
STAGE_ROLE = "role"
STAGE_LISTEN = "listen"
STAGE_ASSESS = "assess"
STAGE_RESPOND = "respond"

ROLE_KEYWORDS = {
    "student": ["student", "university", "college", "school", "studies", "study", "exam", "education", "semester", "notes"],
    "homemaker": ["house wife", "housewife", "home maker", "homemaker", "mother", "mom", "maa", "children", "kids", "baby", "bacha"],
    "parent": ["parent", "mother", "father", "ammi", "abu", "my children", "my kids", "my child"],
    "unemployed": ["unemployed", "no job", "lost my job", "without work", "not working", "no work"],
    "daily_worker": ["worker", "labor", "labour", "construction", "daily wage", "mazdoor", "earner", "jobless"],
    "elderly": ["retired", "old", "elder", "grandfather", "grandmother", "haji", "buzurg", "old man", "old woman"],
    "government": ["government", "employee", "office", "job", "officer", "private employee", "shopkeeper", "business"],
}

GRIEF_INTENT_PATTERNS = [
    r"(should|going to|want to|will).{0,10}(go|join).{0,15}(them|him|her|dead|died|passed)",
    r"she is asking me to come",
    r"he is asking me to come",
    r"(dead|died|passed).{0,20}(asking|calling).{0,15}(come|join|go)",
    r"i should go.{0,15}(join|him|her|them)",
]

PSYCHOSIS_PATTERNS = {
    "command_hallucination": [
        (r"(voice|voices|it|they|someone).{0,20}(told|telling|says?|saying|ordering|commanding).{0,20}(hurt|harm|kill|cut|burn|attack|hit).{0,20}(myself|yourself|someone|them|him|her)", 10),
        (r"(voice|voices).{0,20}(told|telling|says?).{0,20}(end|stop|die|disappear)", 10),
    ],
    "hallucination": [
        (r"(see|seeing|saw|hear|hearing|heard).{0,20}(dead|passed away|died|ghost|spirit).{0,20}(person|people|someone|them|him|her|mother|father|friend)", 8),
        (r"(hear|hearing).{0,20}(voices?|sounds?|music|calling).{0,20}(nobody|no one|not there|not real)", 8),
    ],
    "paranoia": [
        (r"(they|everyone|people|neighbors?).{0,20}(trying|want|planning|going).{0,20}(kill|harm|hurt|poison|attack).{0,20}me", 7),
    ],
    "mania": [
        (r"(haven't|have not|didn't).{0,10}slept?.{0,20}(days?|weeks?).{0,20}(feel (great|amazing|fantastic|energetic|powerful))", 7),
    ],
}

QURAN_HOPE_VERSES = [
    {"ref": "Quran 39:53", "topic": "hope", "arabic": "لَا تَقْنَطُوا مِن رَّحْمَةِ اللَّهِ", "english": "Do not lose hope in Allah's mercy."},
    {"ref": "Quran 2:286", "topic": "burden", "arabic": "لَا يُكَلِّفُ ٱللَّهُ نَفْسًا إِلَّا وُسْعَهَا", "english": "Allah does not burden a soul beyond what it can bear."},
    {"ref": "Quran 94:5-6", "topic": "hardship", "arabic": "فَإِنَّ مَعَ ٱلْعُسْرِ يُسْرًا", "english": "Surely, with hardship comes ease."},
]

PSYCHOSIS_SUPPORT_VERSES = [
    {"ref": "Quran 13:28", "topic": "calm", "arabic": "أَلَا بِذِكْرِ اللَّهِ تَطْمَئِنُّ الْقُلُوبُ", "english": "Surely in the remembrance of Allah do hearts find comfort."},
    {"ref": "Quran 17:82", "topic": "healing", "arabic": "وَنُنَزِّلُ مِنَ ٱلْقُرْءَانِ مَا هُوَ شِفَآءٞ وَرَحْمَةٞ لِّلْمُؤْمِنِينَ", "english": "We send down the Quran as a healing and mercy for the believers."},
    {"ref": "Quran 2:255", "topic": "protection", "arabic": "ٱللَّهُ لَآ إِلَٰهَ إِلَّا هُوَ ٱلْحَيُّ ٱلْقَيُّومُ", "english": "Allah! There is no god worthy of worship except Him, the Ever-Living, All-Sustaining."},
]

SIGNATURE_LINES = [
    "Stay with me for a moment.",
    "Let's hold this gently.",
    "I'm listening with care.",
    "You do not have to carry all of this at once.",
    "Let's keep this moment simple and safe.",
    "We can take this one step at a time."
]

# ─────────────────────────────────────────────
# RETRIEVAL
# ─────────────────────────────────────────────
def clean_text_for_retrieval(text):
    text = str(text).lower()
    text = re.sub(r"http\S+", "", text)
    text = re.sub(r"[^a-zA-Z\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()

def retrieve_from_training_data(user_text, emotion=None, role=None, top_k=2):
    if not RETRIEVAL_DATA:
        return None

    qa_pairs = RETRIEVAL_DATA["qa_pairs"]
    question_vectors = RETRIEVAL_DATA["question_vectors"]

    cleaned = clean_text_for_retrieval(user_text)
    user_vec = tfidf.transform([cleaned])
    sims = cosine_similarity(user_vec, question_vectors)[0].copy()

    for i, pair in enumerate(qa_pairs):
        if pair.get("source") == "idp":
            sims[i] *= 1.8
        if role and pair.get("topic") == role:
            sims[i] *= 1.3
        if emotion and pair.get("topic") == emotion:
            sims[i] *= 1.2

    top_indices = np.argsort(sims)[-top_k:][::-1]
    results = []
    for idx in top_indices:
        if sims[idx] > 0.05:
            results.append({
                "question": qa_pairs[idx]["question"],
                "answer": qa_pairs[idx]["answer"],
                "topic": qa_pairs[idx]["topic"],
                "source": qa_pairs[idx]["source"],
                "similarity": round(float(sims[idx]), 3)
            })

    return results if results else None



# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def init_session():
    current_language = session.get("language", "en")
    
    session["stage"] = STAGE_NAME
    session["name"] = ""
    session["language"] = current_language
    session["role"] = "default"
    session["history"] = []
    session["phq"] = {}
    session["gad"] = {}
    session["who"] = {}
    session["q_index"] = 0
    session["is_positive"] = False
    session["emotion"] = "unknown"
    session["confidence"] = 0.0
    session["severity"] = "Unknown"
    session["assessment_started"] = False
    session["crisis_active"] = False
    session["crisis_level"] = "safe"
    session["used_verses"] = []
    session["used_psychosis_verses"] = []
    session["displacement_cause"] = "displacement"
    session["idp_hardships"] = []
    session["user_id"] = None
    session["is_returning_user"] = False
    session["last_assessment_scores"] = {}
    session["last_advice"] = ""
    session["sa_followup_count"] = 0
    session["sa_followup_needed"] = False
    session["sa_listen_turns"] = 0
    session["sa_dominant_concern"] = "minimal"
    session["crisis_step"] = ""
    session["crisis_safe_turns"] = 0

def add_to_history(role, content):
    history = list(session.get("history", []))
    history.append({"role": role, "content": content})
    session["history"] = history[-14:]

def detect_role(text):
    text = text.lower().strip()
    for role, keywords in ROLE_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return role
    return "default"


def detect_family_stress(msg):
    """Detect when the user is describing family pressure/stress.

    Neutral phrases like "my family is with me" should not count as stress.
    """
    msg = (msg or "").lower()

    negative_words = [
        "stress", "problem", "issue", "pressure",
        "fight", "argument", "burden", "responsibility",
        "tension", "difficult", "hard", "toxic",
        "children pressure", "kids pressure", "parents pressure",
        "family responsibility", "too much responsibility"
    ]

    if "family" in msg and any(word in msg for word in negative_words):
        return True

    return any(phrase in msg for phrase in [
        "children pressure", "kids pressure", "parents pressure",
        "family responsibility", "too much responsibility",
        "taking care of family", "burden of family", "supporting family"
    ])


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .core import (  # noqa: E402
    RETRIEVAL_DATA,
    tfidf,
)
from .database import (  # noqa: E402
    get_db,
)

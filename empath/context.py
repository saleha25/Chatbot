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
# Reads sleep hours, mood and energy from what the user writes
# (English, Urdu script and Roman Urdu). Rules:
#  - whole-word matching (so "rest of the family" is not sleep, "downtown" is not sad)
#  - negation aware ("not good" = low mood, "not tired" = nothing saved)
#  - sleep hours only when sleep is actually talked about ("walked 3 hours" is not sleep)
#  - hopeless / suicidal statements count as very low mood
# ─────────────────────────────────────────────

_URDU_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_NEGATION_RE = r"(?:not|no|never|isn'?t|aren'?t|wasn'?t|don'?t|doesn'?t|didn'?t|can'?t|nahi|nahin|nai|na)"


def _wb_normalize(text):
    t = (text or "").translate(_URDU_DIGITS).lower()
    t = t.replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", t)


def _has(patterns, t):
    return any(re.search(p, t) for p in patterns)


def _negated(word_re, t):
    """True if the word appears right after a negation: 'not (feeling) good', 'nahi khush'."""
    return bool(
        re.search(rf"\b{_NEGATION_RE}\b(?:\s+\w+){{0,2}}?\s+{word_re}", t)
        or re.search(rf"{word_re}\s+(?:\w+\s+){{0,1}}(?:nahi|nahin|nai)\b", t)  # Roman Urdu: "khush nahi"
    )


# ---------- MOOD ----------
MOOD_VERY_LOW = [
    # hopeless / despair
    r"\bhopeless", r"\bno hope\b", r"\blost (all )?hope\b", r"\bnothing (is going to|will|ever) (get|be) better",
    r"\bnever (going to )?get better", r"\bwon'?t get better", r"\bwhat'?s the point\b", r"\bno point\b",
    r"\bnothing matters\b", r"\bgive up\b", r"\bgiving up\b", r"\bcan'?t go on\b", r"\bcan'?t take (it|this) anymore\b",
    r"\bdepressed\b", r"\bvery sad\b", r"\bso sad\b", r"\breally sad\b", r"\bextremely sad\b", r"\bbroken\b",
    r"\bempty inside\b", r"\bi feel (dead|empty|numb)\b", r"\bworthless\b", r"\bmiserable\b",
    r"\bmood is (very|really|so|extremely|too) (bad|low|down)\b", r"\b(terrible|awful|horrible|worst) (mood|day|feeling)\b",
    r"\b(mood|feel|feeling|i'?m|i am) (is )?(terrible|awful|horrible)\b", r"\bmood (bohat|bohot|bahut) (kharab|bura)\b",
    r"موڈ بہت خراب", r"بہت برا محسوس",
    # suicidal
    r"\bkill (my ?self|me)\b", r"\bwant(ed)? to die\b", r"\bend(ing)? my life\b", r"\bsuicid",
    r"\bdon'?t want to (live|be alive)\b", r"\bbetter off dead\b", r"\bend it all\b",
    # Roman Urdu
    r"\b(bohat|bohot|bahut|bht|boht) (zyada )?(udaas|udas|mayoos|mayus|pareshan)\b", r"\bkoi (umeed|umid|ummeed)\b.*\bnahi",
    r"\b(umeed|umid) khatam\b", r"\bkuch (theek|thik|behtar) nahi? hoga\b", r"\bmarna chaht", r"\bkhud ?kushi\b",
    r"\bjeena nahi? chaht", r"\bjeene ka (koi )?(faida|fayda|dil) nahi",
    # Urdu
    r"بہت اداس", r"بہت زیادہ اداس", r"بہت مایوس", r"کوئی امید نہیں", r"امید ختم", r"کچھ ٹھیک نہیں ہوگا",
    r"کچھ بہتر نہیں ہوگا", r"خودکشی", r"مرنا چاہت", r"جینے کا دل نہیں", r"جینے کا کوئی فائدہ نہیں", r"ٹوٹ گئی ہوں",
    r"ٹوٹ گیا ہوں", r"بہت زیادہ پریشان",
]
MOOD_ANXIOUS = [
    r"\banxious\b", r"\banxiety\b", r"\bworried\b", r"\bworry(ing)?\b", r"\bscared\b", r"\bafraid\b", r"\bpanic",
    r"\bnervous\b", r"\bfrightened\b", r"\bterrified\b", r"\bon edge\b", r"\bstressed\b",
    r"\bghabrahat\b", r"\bghabra (raha|rahi|gaya|gayi)", r"\bbechain\b", r"\bdar lag", r"\bfikar\b", r"\btension\b",
    r"گھبراہٹ", r"بے چین", r"بے چینی", r"فکر ہو رہی", r"بہت فکر", r"ڈر لگ", r"خوف", r"ٹینشن",
]
MOOD_LOW = [
    r"\bsad\b", r"\bunhappy\b", r"\bupset\b", r"\bfeeling down\b", r"\bfeel down\b", r"\blow mood\b", r"\bmood is (low|bad)\b",
    r"\bmood is (bad|low|down|off|not great)\b", r"\b(feel|feeling|felt) (bad|low|down|off)\b", r"\bnot in a good mood\b",
    r"\bbad mood\b", r"\bmood (kharab|off|bura|theek nahi)\b", r"\bbura (lag|mehsoos)", r"موڈ (خراب|ٹھیک نہیں|اچھا نہیں)", r"برا محسوس",
    r"\bbad day\b", r"\brough day\b", r"\bdifficult day\b", r"\bhard day\b", r"\blonely\b", r"\balone\b", r"\bcrying\b",
    r"\bcried\b", r"\bangry\b", r"\bfrustrated\b", r"\bhurt\b", r"\bheartbroken\b", r"\bmiss (my )?(home|family)\b",
    r"\bhomesick\b", r"\bdisappointed\b",
    r"\budaas\b", r"\budas\b", r"\bmayoos\b", r"\bdil nahi lag", r"\bro (raha|rahi)\b", r"\brona aa", r"\bghussa\b",
    r"\bakela\b", r"\bakeli\b", r"\bdukh\b", r"\bdukhi\b", r"\bmood (kharab|off)\b", r"\bbura din\b", r"\bpareshan\b",
    r"اداس", r"مایوس", r"دل نہیں لگ", r"موڈ خراب", r"برا دن", r"دن برا", r"دن اچھا نہیں", r"اچھا محسوس نہیں",
    r"طبیعت اچھی نہیں", r"پریشان", r"غصہ", r"اکیلا", r"اکیلی", r"تنہا", r"دکھی", r"رونا آ",
]
_POSITIVE_WORDS = r"(good|great|happy|better|fine|okay|ok|well|calm|relaxed|peaceful|khush|theek|thik|acha|achha|behtar)"
MOOD_GOOD = [
    r"\bhappy\b", r"\bglad\b", r"\bgreat\b", r"\bwonderful\b", r"\bcalm\b(?! down)", r"\brelaxed\b", r"\bpeaceful\b",
    r"\bexcited\b", r"\bgrateful\b", r"\bthankful\b", r"\bproud\b", r"\bjoy\b",
    r"\b(feel|feeling|felt|am|i'?m) (much |a (little|bit) |so |really )?(better|good|great)\b", r"\bgood day\b", r"\bgreat day\b",
    r"\bmood is (good|great|nice|better|happy|amazing)\b", r"\bgood mood\b", r"\bmood (acha|achha|accha|behtar)\b",
    r"موڈ (اچھا|بہتر)",
    r"\bkhush\b", r"\bbehtar (mehsoos|lag)", r"\bacha (lag|mehsoos)", r"\bachha (lag|mehsoos)", r"\bsukoon\b", r"\balhamdulillah\b",
    r"خوش", r"بہتر محسوس", r"اچھا محسوس کر رہ", r"دن اچھا (تھا|گزرا)", r"پرسکون", r"سکون", r"الحمدللہ",
]
MOOD_OKAY = [
    r"\b(i'?m|i am|feel|feeling) (okay|ok|fine|alright|all right)\b", r"\bnot (too )?bad\b", r"\bso-so\b",
    r"\b(theek|thik) (hoon|hun|hu|hai)\b", r"ٹھیک ہوں", r"ٹھیک ہے", r"معمول کے مطابق",
    r"\bmood is (okay|ok|fine|alright|normal)\b", r"\bmood (theek|thik|normal) (hai|he)\b", r"موڈ (ٹھیک|نارمل)",
]


def extract_mood(text):
    t = _wb_normalize(text)
    if _has(MOOD_VERY_LOW, t):
        return "very_low"
    if _has(MOOD_ANXIOUS, t) and not _negated(r"(anxious|worried|scared|afraid|nervous|stressed)", t):
        return "anxious"
    # "not good", "not happy", "khush nahi" -> low mood
    if _negated(_POSITIVE_WORDS, t) and not re.search(r"\bnot (too )?bad\b", t):
        return "low"
    if _has(MOOD_LOW, t) and not _negated(r"(sad|upset|lonely|alone|angry|udaas|udas|pareshan)", t):
        return "low"
    if _has(MOOD_GOOD, t):
        return "good"
    if _has(MOOD_OKAY, t):
        return "okay"
    return None


# ---------- ENERGY ----------
ENERGY_VERY_LOW = [
    r"\bexhausted\b", r"\bdrained\b", r"\bno energy\b", r"\bzero energy\b", r"\bburnt? out\b", r"\bdead tired\b",
    r"\bcompletely tired\b", r"\bcan'?t (even )?(move|get out of bed)\b",
    r"\b(bilkul|koi) (energy|taqat|himmat) nahi", r"\bnidhal\b", r"\b(bohat|bohot|bahut) zyada thak",
    r"بالکل توانائی نہیں", r"بہت زیادہ تھک", r"انتہائی تھک", r"نڈھال", r"بالکل ہمت نہیں",
]
ENERGY_LOW = [
    r"\btired\b", r"\bweak\b", r"\bfatigue", r"\blow energy\b", r"\benergy is (low|bad)\b", r"\bsleepy\b", r"\blazy\b",
    r"\bno motivation\b", r"\bunmotivated\b",
    r"\bthak (gaya|gayi|gai|gya|gyi)\b", r"\bthaka\b", r"\bthaki\b", r"\bthakan\b", r"\bthakawat\b", r"\bkamzori\b",
    r"\bhimmat nahi", r"\benergy nahi", r"\bkuch karne ka dil nahi",
    r"تھکا", r"تھکی", r"تھکن", r"تھکاوٹ", r"کمزوری", r"انرجی کم", r"توانائی کم", r"کچھ کرنے کا دل نہیں", r"ہمت نہیں",
]
ENERGY_MEDIUM = [
    r"\benergy (is |feels )?(normal|okay|ok|fine|alright)\b", r"\bnormal energy\b",
    r"انرجی (ٹھیک|نارمل)", r"توانائی (ٹھیک|معمول)",
]
ENERGY_HIGH = [
    r"\benergetic\b", r"\bfull of energy\b", r"\blots of energy\b", r"\bmotivated\b", r"\brefreshed\b", r"\bfresh\b",
    r"\benergy is (good|high|great)\b", r"\bactive\b",
    r"\b(bohat|bohot) energy\b", r"\btaza dam\b", r"انرجی اچھی", r"توانائی اچھی", r"تازہ دم", r"چاق و چوبند", r"پُرجوش",
]


def extract_energy(text):
    t = _wb_normalize(text)
    if _negated(r"(tired|exhausted|weak|thaka|thaki)", t) or re.search(r"\b(thak|thaka|thaki)\w*\s+nahi", t):
        return None  # "not tired" says nothing reliable about energy
    if _has(ENERGY_VERY_LOW, t):
        return "very_low"
    if _has(ENERGY_LOW, t):
        return "low"
    if _has(ENERGY_HIGH, t) and not _negated(r"(energetic|motivated|active|fresh)", t):
        return "high"
    if _has(ENERGY_MEDIUM, t):
        return "medium"
    return None


# ---------- SLEEP ----------
SLEEP_WORDS_RE = (
    r"(\bsleep\w*|\bslept\b|\binsomnia\b|\bnap\b|\bnightmares?\b|\bawake\b|\bneend\b|\bnind\b|\bnend\b|\bsoya\b|\bsoyi\b"
    r"|\bsoi\b|\bso (nahi|nai) (paa?ya|paa?yi|saka|saki|sakta|sakti)\b|\bjaag\w*|نیند|سویا|سوئی|سو نہیں|جاگ)"
)
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5, "chhe": 6, "che": 6,
    "saat": 7, "sat": 7, "aath": 8, "aat": 8, "nau": 9, "das": 10,
    "ایک": 1, "دو": 2, "تین": 3, "چار": 4, "پانچ": 5, "چھ": 6, "سات": 7, "آٹھ": 8, "نو": 9, "دس": 10,
    "گیارہ": 11, "بارہ": 12,
}
_HOURS_RE = r"(?:hours?|hrs?|ghant(?:e|ay|a|on|ey)|گھنٹے|گھنٹہ|گھنٹوں)"
_NUM_RE = r"(\d+(?:\.\d+)?|" + "|".join(sorted(map(re.escape, _NUM_WORDS), key=len, reverse=True)) + r")"


def _to_hours(value):
    try:
        return float(value)
    except ValueError:
        return float(_NUM_WORDS.get(value, -1))


def extract_sleep_hours(text):
    t = _wb_normalize(text)
    if not re.search(SLEEP_WORDS_RE, t):
        return None  # hours that are not about sleep ("walked 3 hours") are ignored

    # "couldn't sleep at all", "no sleep", "didn't sleep", "neend bilkul nahi aayi"
    if re.search(r"\b(couldn'?t|could not|didn'?t|did not|can'?t) sleep at all\b|\bno sleep\b|\bnot slept\b"
                 r"|\bawake (all|the whole) night\b|\bneend bilkul nahi\b|\b(saari|sari|puri|poori) raat jaag"
                 r"|\b(raat bhar|saari raat|sari raat|puri raat|poori raat) (neend|nind) (nahi|nai)\b"
                 r"|بالکل نیند نہیں|ساری رات جاگ|رات بھر نیند نہیں|ساری رات نیند نہیں|پوری رات نیند نہیں", t):
        return 0.0

    # Look at each sentence that talks about sleep, and take a number of hours from it
    for sentence in re.split(r"[.!?؟۔\n]+", t):
        if not re.search(SLEEP_WORDS_RE, sentence):
            continue
        sleep_spots = [m.start() for m in re.finditer(SLEEP_WORDS_RE, sentence)]
        best = None
        # "worked 10 hours and slept 5 hours" -> take the number closest to a sleep word
        for m in re.finditer(rf"(?<![\w.]){_NUM_RE}\s*(?:-|to|or|se)?\s*(?:\d+\s*)?{_HOURS_RE}", sentence):
            hours = _to_hours(m.group(1))
            if not 0 <= hours <= 24:
                continue
            distance = min(abs(m.start() - p) for p in sleep_spots)
            if best is None or distance < best[0]:
                best = (distance, hours)
        if best:
            return best[1]
    return None


# ---------- COMBINED ----------
_MOOD_HINT_RE = r"\b(mood|feel|feeling|felt|sad|happy|down|hopeless|depressed|anxious|upset|udaas|khush|pareshan)\b|موڈ|اداس|خوش|پریشان|مایوس|محسوس"
_ENERGY_HINT_RE = r"\b(energy|tired|exhausted|drained|fatigue|weak|thak\w*|kamzori|himmat)\b|توانائی|انرجی|تھک|کمزوری|نڈھال|ہمت"


def extract_wellbeing_info(text):
    t = _wb_normalize(text)
    sleep_hours = extract_sleep_hours(text)
    mood = extract_mood(text)
    energy = extract_energy(text)

    mentioned_sleep = sleep_hours is not None or bool(re.search(SLEEP_WORDS_RE, t))
    mentioned_mood = mood is not None or bool(re.search(_MOOD_HINT_RE, t))
    mentioned_energy = energy is not None or bool(re.search(_ENERGY_HINT_RE, t))

    return {
        "sleep_hours": sleep_hours,
        "mood": mood,
        "energy": energy,
        "mentioned_sleep": mentioned_sleep,
        "mentioned_mood": mentioned_mood,
        "mentioned_energy": mentioned_energy,
        "has_any": any([mentioned_sleep, mentioned_mood, mentioned_energy]),
        # True only when there is an actual value worth saving on the Wellness page
        "has_values": any(v is not None for v in (sleep_hours, mood, energy)),
    }


# ─────────────────────────────────────────────
# SMART WELLBEING READING (AI understands the message)
# The user does not have to say "my mood is low". The AI reads what they
# wrote (English, Urdu or Roman Urdu), together with their last two messages,
# and judges mood, energy and sleep the way a counsellor would.
# The keyword reader above is only the backup when the API is not available.
# One tiny gpt-4o-mini call per message (~60 output tokens).
# ─────────────────────────────────────────────
WELLBEING_MOODS = {"very_low", "low", "anxious", "okay", "good"}
WELLBEING_ENERGY = {"very_low", "low", "medium", "high"}

WELLBEING_READER_PROMPT = """
You read ONE message from a user of a mental-health support chat for displaced people in Pakistan
(English, Urdu or Roman Urdu) and judge their CURRENT state, like a careful counsellor would.
The user usually does NOT name their mood. Infer it from what they describe and how they say it.

Return ONLY JSON: {"mood": ..., "energy": ..., "sleep_hours": ...}

mood (how they feel emotionally right now):
  "very_low" = despair, hopelessness, wanting to die/give up, deep grief, feeling broken or worthless
  "low"      = sad, hurt, lonely, angry, frustrated, ignored, homesick, disappointed, crying
  "anxious"  = worried, scared, nervous, panicky, afraid of what will happen, frightening experiences
  "okay"     = neutral or mixed, "fine", getting by
  "good"     = happy, relieved, grateful, proud, calm, hopeful, feeling better
  null       = the message gives NO emotional information (e.g. "ok", "thanks", a recipe question, a factual question)
energy (physical/mental energy):
  "very_low" = exhausted, drained, can't get out of bed, no strength at all
  "low"      = tired, weak, sleepy, no motivation
  "medium"   = normal energy
  "high"     = energetic, active, refreshed, full of energy
  null       = not described or implied
sleep_hours (number):
  only when the user says how long they slept (e.g. "3 ghante soi" -> 3, "slept around 8 hours" -> 8),
  0 if they say they did not sleep at all / were awake the whole night,
  otherwise null. Hours of work/walking/travel are NOT sleep.

Rules:
- Judge the CURRENT message. Earlier messages are only context (e.g. "yes, every night" after talking about nightmares).
- Do not guess: if unsure, use null.
- Negation matters: "not happy" is low, "not tired" gives energy null.
- Asking for help with a task (recipe, information) says nothing about mood unless feelings are expressed.

Examples:
"Everyone in the camp ignores us" -> {"mood": "low", "energy": null, "sleep_hours": null}
"aaj ka din kuch khaas nahi tha, bas guzar gaya" -> {"mood": "okay", "energy": null, "sleep_hours": null}
"main ab khushi mehsoos kr rahi hon" -> {"mood": "good", "energy": null, "sleep_hours": null}
"raat bhar karwatein badalti rahi, subah uthne ka dil nahi kiya" -> {"mood": "low", "energy": "low", "sleep_hours": 0}
"I finally got my ID card made today!" -> {"mood": "good", "energy": null, "sleep_hours": null}
"مجھے ڈر ہے کہ کل کیا ہوگا، بچوں کی فیس کہاں سے آئے گی" -> {"mood": "anxious", "energy": null, "sleep_hours": null}
"I walked 3 hours to the clinic and slept maybe 4 hours" -> {"mood": null, "energy": null, "sleep_hours": 4}
"biryani ki recipe batao" -> {"mood": null, "energy": null, "sleep_hours": null}
""".strip()

_SMART_WELLBEING_CACHE = {}


def _recent_user_messages_for_context(current, limit=2):
    try:
        history = session.get("history", []) or []
    except RuntimeError:
        return []
    msgs = [str(h.get("content", "")) for h in history if h.get("role") == "user"]
    if msgs and msgs[-1].strip() == (current or "").strip():
        msgs = msgs[:-1]
    return [m[:300] for m in msgs[-limit:]]


def _ai_read_wellbeing(text):
    """Ask the model; returns {"mood","energy","sleep_hours"} or None on any problem."""
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    earlier = _recent_user_messages_for_context(text)
    user_block = ""
    if earlier:
        user_block += "Earlier messages (context only):\n" + "\n".join(f"- {m}" for m in earlier) + "\n\n"
    user_block += f"CURRENT message:\n{str(text)[:800]}"
    try:
        result = client.chat.completions.create(
            model=os.environ.get("EMPATH_AI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": WELLBEING_READER_PROMPT},
                      {"role": "user", "content": user_block}],
            temperature=0,
            max_tokens=60,
            response_format={"type": "json_object"},
        )
        data = json.loads(result.choices[0].message.content or "{}")
    except Exception as e:
        print("Wellbeing AI reader error (using keyword backup):", e)
        return None

    mood = data.get("mood")
    energy = data.get("energy")
    sleep = data.get("sleep_hours")
    mood = mood if mood in WELLBEING_MOODS else None
    energy = energy if energy in WELLBEING_ENERGY else None
    try:
        sleep = float(sleep) if sleep is not None else None
        if sleep is not None and not 0 <= sleep <= 24:
            sleep = None
    except (TypeError, ValueError):
        sleep = None
    return {"mood": mood, "energy": energy, "sleep_hours": sleep}


def extract_wellbeing_info_smart(text):
    """Same result shape as extract_wellbeing_info, but the AI does the reading.

    The keyword reader still runs: it fills in only what the AI left empty,
    and it is the full backup when the AI call fails.
    """
    key = (text or "").strip()
    if key in _SMART_WELLBEING_CACHE:
        return dict(_SMART_WELLBEING_CACHE[key])

    keyword_result = extract_wellbeing_info(text)

    # Very short messages ("ok", "hmm", "thanks") carry no state: skip the API call
    if len(key) < 4:
        return keyword_result

    ai = _ai_read_wellbeing(text)
    if ai is None:
        result = keyword_result
    else:
        result = dict(keyword_result)
        for field in ("mood", "energy", "sleep_hours"):
            if ai[field] is not None:
                result[field] = ai[field]
        result["mentioned_mood"] = keyword_result["mentioned_mood"] or result["mood"] is not None
        result["mentioned_energy"] = keyword_result["mentioned_energy"] or result["energy"] is not None
        result["mentioned_sleep"] = keyword_result["mentioned_sleep"] or result["sleep_hours"] is not None
        result["has_any"] = any([result["mentioned_mood"], result["mentioned_energy"], result["mentioned_sleep"]])
        result["has_values"] = any(result[f] is not None for f in ("mood", "energy", "sleep_hours"))
        result["read_by"] = "ai"

    if len(_SMART_WELLBEING_CACHE) > 200:
        _SMART_WELLBEING_CACHE.clear()
    _SMART_WELLBEING_CACHE[key] = dict(result)
    return result


def save_wellbeing_if_mentioned(user_id, user_message, mood_if_missing=None):
    """Save sleep / mood / energy from this message into today's wellbeing log.

    mood_if_missing: used for crisis messages, which always mean a very low mood
    even when the wording is not in our keyword lists.
    """
    if not user_id:
        return None

    extracted = extract_wellbeing_info_smart(user_message)
    if mood_if_missing and not extracted.get("mood"):
        extracted["mood"] = mood_if_missing
        extracted["mentioned_mood"] = True
        extracted["has_any"] = True
        extracted["has_values"] = True

    # Save only when we actually found a value (sleep hours, mood or energy).
    if not extracted.get("has_values"):
        return None

    # Local (Pakistan) time, so the Wellness page shows the right day and time
    now_local = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT * FROM wellbeing_logs
        WHERE user_id = ?
        AND date(log_date) = date(?)
        ORDER BY id DESC
        LIMIT 1
    """, (user_id, now_local))

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
            SET sleep_hours = ?, mood = ?, energy = ?, source_text = ?, log_date = ?
            WHERE id = ?
        """, (
            sleep, mood, energy, new_source, now_local, today_log["id"]
        ))

    else:
        cursor.execute("""
            INSERT INTO wellbeing_logs
                (user_id, sleep_hours, mood, energy, source_text, log_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            extracted["sleep_hours"],
            extracted["mood"],
            extracted["energy"],
            user_message,
            now_local
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
    client,
    tfidf,
)
from .database import (  # noqa: E402
    get_db,
)
"""Safety: crisis / self-harm detection (English, Urdu, Roman Urdu), guardrails, crisis replies.

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


def direct_crisis_override(text):
    t = (text or "").lower()
    high_risk = [
        # Direct statements
        "i want to die", "want to die", "i want to kill myself", "kill myself",
        "commit suicide", "suicide", "end my life", "end it all",
        "i don't want to live", "i do not want to live", "don't want to live anymore",
        "no point in living", "no reason to live", "better off dead",
        "i will kill myself", "i am going to kill myself", "i should kill myself",
        # Indirect / method signals
        "not wake up", "never wake up", "don't want to wake up", "do not want to wake up",
        "sleep forever", "sleeping pills", "overdose", "take all the pills", "too many pills",
        # Urdu
        "مرنا چاہتا", "مرنا چاہتی", "خودکشی", "زندگی ختم کر", "جینے کا دل نہیں",
        "جینا نہیں چاہتا", "جینا نہیں چاہتی", "نیند کی گولیاں", "دوبارہ نہ اٹھوں",
        "مر جاؤں", "مر جاوں", "جینے کا کوئی فائدہ نہیں", "جینے کا فائدہ نہیں",
        "سب ختم کر دوں", "خود کو ختم", "خود کو مار", "موت آ جائے",
    ]
    if any(phrase in t for phrase in high_risk):
        return True

    # Roman Urdu (spelling varies a lot, so regex patterns)
    return any(re.search(p, t) for p in ROMAN_URDU_CRISIS_PATTERNS)


# Roman Urdu suicide / self-harm signals.
# Written as regex because people spell Roman Urdu in many ways
# (jeene / jeenay / jine, mar jaun / mar jaon / mar jaoon ...).
ROMAN_URDU_CRISIS_PATTERNS = [
    r"\bmar\s*ja(u|un|o|on|oon|aun|aon|aoon|ana|na)\b",
    r"\bmarna\s*(chahta|chahti|chahata|chahati|chahtA|chahta\s*hun|hai|he)\b",
    r"\bmar\s*jana\s*(chahta|chahti|chahiye|chahye)\b",
    r"\b(jeene|jeenay|jeeny|jine|jinay|jeena|jina|jeeney)\s*(ka|ki|ko|ka\s*koi|ki\s*koi)?\s*(koi\s*)?(faida|faidah|fayda|faeda|maqsad|dil|mann|man|wajah)\s*(nahi|nai|nahin|ni|nhi)\b",
    r"\b(jeena|jina|jeene|jine)\s*nahi\s*(chahta|chahti|hai|he)\b",
    r"\bkhud\s*-?\s*k(u|a)shi\b",
    r"\bzindagi\s*(khatam|khtm|khatm)\b",
    r"\bsab\s*(kuch\s*)?(khatam|khtm|khatm)\s*kar\s*(dun|doon|du|don|deta|deti|dena|lun|loon)\b",
    r"\b(neend|nind|neind)\s*ki\s*(goli|golian|goliyan|goliyaan|golyan)\b",
    r"\b(dobara|kabhi)\s*na\s*uth(u|un|oon|on|un)\b",
    r"\buthna\s*(hi\s*)?nahi\s*(chahta|chahti)\b",
    r"\bkhud\s*ko\s*(khatam|maar|mar|nuqsan|nuksan|nuqsaan)\b",
    r"\b(maut|mout|mot)\s*(aa\s*jaye|a\s*jaye|chahiye|chahye|chahta|chahti)\b",
]

def check_guardrail(user_text):
    original = user_text.lower().strip()
    normalized = normalize_text(original)

    for text in [original, normalized]:
        for combo in BLOCKED_TOPICS:
            if all(w in text for w in combo):
                return {
                    "blocked": True,
                    "response": "I care about your wellbeing, so I cannot help with that. But I can listen to what you are going through right now."
                }
        for pattern in (FRAMING_PATTERNS + JAILBREAK_PATTERNS + ESCALATION_PATTERNS + SYNONYM_PATTERNS + MANIPULATION_PATTERNS):
            if re.search(pattern, text):
                return {
                    "blocked": True,
                    "response": "My purpose is to support your wellbeing. I cannot be redirected from that. If you want to talk, I am here."
                }
    return {"blocked": False}

def detect_crisis(text):
    t = text.lower().strip()
    scores = {k: 0 for k in CRISIS_PATTERNS}

    for level, patterns in CRISIS_PATTERNS.items():
        for pattern, weight in patterns:
            if re.search(pattern, t):
                scores[level] += weight

    finality = sum(3 for w in FINALITY_WORDS if w in t)
    action = sum(3 for w in ACTION_WORDS if w in t)
    release = sum(4 for w in RELEASE_WORDS if w in t)
    bonus = finality + action + release

    emergency = scores.get("emergency", 0) + bonus
    crisis = scores.get("crisis", 0) + scores.get("grief_crisis", 0) + scores.get("metaphor_crisis", 0) + scores.get("farewell", 0) + bonus
    self_harm = scores.get("self_harm", 0)
    distress = scores.get("distress", 0)

    enjoyment = any(w in t for w in ["feel good", "like", "enjoy", "love", "relief", "calm", "better"])

    if emergency >= 10:
        return "emergency"
    if self_harm >= 7 and not enjoyment:
        return "emergency"
    if self_harm >= 4 and enjoyment:
        return "crisis"
    if crisis >= 8:
        return "crisis"
    if self_harm >= 4:
        return "crisis"
    if crisis >= 4 or distress >= 7:
        return "borderline"
    if distress >= 3:
        return "distress"
    return "safe"

def detect_emotion(text):
    try:
        cleaned = clean_text(text)
        vec = tfidf.transform([cleaned])
        emotion = emotion_model.predict(vec)[0]
        proba = emotion_model.predict_proba(vec)[0]
        conf = round(float(max(proba)) * 100, 1)
        return emotion, conf
    except Exception as e:
        print("Emotion error:", e)
        return "emotional_support", 50.0
    
    

def detect_psychosis(text):
    """Detect psychosis-like experiences without diagnosing the user.

    Categories include hallucination, paranoia, delusional belief, disorganized/confusing thought,
    and grief-related dream content that escalates into a belief/urge to join a deceased person.
    """
    t = normalize_for_safety(text)
    detected = {}

    # A memory of real events ("I saw dead bodies in the flood") is trauma, not psychosis
    if re.search(r"\b(saw|seen|had seen|watched)\b.{0,30}\b(dead|bodies|body|died|killed)\b", t) and re.search(
        r"\b(flood|floods|earthquake|war|bomb|blast|attack|accident|operation|fighting|camp|last year|years ago|when)\b", t
    ) and not re.search(r"\b(still see|keep seeing|now see|calling me|talking to me)\b", t):
        return {"psychosis_detected": False, "level": "safe"}

    expanded_patterns = {
        "command_hallucination": [
            (r"(voice|voices|someone|they).{0,25}(told|telling|says?|saying|ordering|commanding).{0,30}(hurt|harm|kill|cut|burn|attack|hit|end|die|go|join)", 12),
            (r"(dead|died|passed away|deceased).{0,30}(calling|asking|telling).{0,30}(come|go|join)", 12),
            (r"(calling me).{0,30}(i should|should|want to|need to).{0,30}(go|join|come)", 11),
        ],
        "hallucination": [
            (r"\b(hear|hearing|heard)\b.{0,25}\b(voice|voices|calling|sounds?)\b", 8),
            (r"\b(see|seeing|saw)\b.{0,25}\b(person|people|shadow|figure|dead|ghost|spirit|someone)\b", 8),
            (r"\b(dead|died|passed away|deceased)\b.{0,25}\b(calling me|talking to me|speaking to me)\b", 8),
        ],
        "paranoia": [
            (r"\b(they|people|someone|neighbors?|everyone)\b.{0,30}\b(watching|following|tracking|spying|monitoring)\b.{0,20}\b(me|my family)?\b", 7),
            (r"\b(they|people|someone|neighbors?)\b.{0,30}\b(trying|planning|want).{0,20}(kill|harm|hurt|poison|attack)\b.{0,20}\bme\b", 9),
        ],
        "delusional_belief": [
            (r"\b(i know|i am sure|definitely|for sure)\b.{0,35}\b(controlling my mind|reading my thoughts|sending messages|special powers|chosen by)\b", 7),
            (r"\b(tv|radio|phone|signs|messages)\b.{0,30}\b(talking to me|sending me|warning me|controlling me)\b", 7),
        ],
        "disorganized_confusion": [
            (r"\b(my thoughts|mind)\b.{0,25}\b(broken|scattered|mixed up|not making sense|confused)\b", 4),
            (r"\b(i cannot tell|can't tell)\b.{0,25}\b(real|dream|true)\b", 6),
        ],
        "seeing_deceased": [(p, 7) for p in SEES_DECEASED_PATTERNS],
        "sensing_presence": [(p, 7) for p in PRESENCE_PATTERNS],
        # ----- Urdu script -----
        "command_hallucination_ur": [
            (r"آواز.{0,30}(کہتی|کہہ رہی|کہتے|حکم).{0,30}(مار|نقصان|ختم|جان|چلی جاؤ|آ جاؤ)", 12),
        ],
        "hallucination_ur": [
            (r"آواز(یں|وں)?.{0,15}(سنائی|سنتی|سنتا|سن رہی|سن رہا)", 8),
            (r"(فوت|مر چک|مرحوم).{0,30}(بلا رہ|بلاتی|بلاتے|بات کر)", 8),
        ],
        "paranoia_ur": [
            (r"(لوگ|کوئی|سب|ہمسائے|پڑوسی).{0,30}(پیچھا|نگرانی|جاسوسی|مجھ پر نظر)", 7),
            (r"(لوگ|کوئی|سب).{0,30}(مارنا|زہر|نقصان پہنچانا).{0,20}(چاہت|دینا)", 9),
        ],
        "delusional_belief_ur": [
            (r"(میرے خیالات|میرا دماغ|میرے دماغ).{0,20}(پڑھ|کنٹرول|قابو)", 7),
        ],
        # ----- Roman Urdu -----
        "command_hallucination_ru": [
            (r"\b(awaz|awaaz|awazein|awaazein|awazain)\b.{0,30}\b(kehti|keh rahi|kehte|hukam)\b.{0,30}\b(maar|nuqsan|khatam|jaan|aa jao|chali jao)", 12),
        ],
        "hallucination_ru": [
            (r"\b(awaz|awaaz|awazein|awaazein|awazain)\b.{0,15}\b(sunai|sunti|sunta|sun rahi|sun raha)\b", 8),
        ],
        "paranoia_ru": [
            (r"\b(log|koi|sab|hamsaye|parosi)\b.{0,30}\b(peecha|picha|peechha|pecha|nigrani|jasoosi)\b", 7),
            (r"\b(log|koi|sab)\b.{0,30}\b(maarna|zehar|zeher|nuqsan)\b.{0,20}\b(chahte|chahta|chahti|dena)\b", 9),
        ],
        "delusional_belief_ru": [
            (r"\b(mere khayal|mera dimagh|mera dimag|mere dimagh)\b.{0,20}\b(parh|control|qaboo)", 7),
        ],
    }

    # Keep the original trained/loaded patterns too, if present.
    for category, patterns in PSYCHOSIS_PATTERNS.items():
        score = 0
        for pattern, weight in patterns:
            if re.search(pattern, t):
                score += weight
        if score > 0:
            detected[category] = detected.get(category, 0) + score

    t_plain = _strip_madda(t)  # "نظر اتے" (voice typing) = "نظر آتے"
    for category, patterns in expanded_patterns.items():
        score = 0
        for pattern, weight in patterns:
            if re.search(pattern, t) or re.search(_strip_madda(pattern), t_plain):
                score += weight
        if score > 0:
            detected[category] = detected.get(category, 0) + score

    try:
        if not detected and _is_perception_followup(text):
            detected["perception_followup"] = 7
    except RuntimeError:
        pass  # no request/session (e.g. offline tests)

    if not detected:
        return {"psychosis_detected": False, "level": "safe"}

    top_category = max(detected, key=detected.get)
    top_score = detected[top_category]

    if top_category.startswith("command_hallucination") or top_score >= 11:
        level = "EMERGENCY"
    elif top_score >= 7:
        level = "CRISIS"
    else:
        level = "DISTRESS"

    return {
        "psychosis_detected": True,
        "category": top_category,
        "score": top_score,
        "level": level,
    }


def detect_dream_or_hallucination_risk(text):
    """Separate grief dreams from possible psychosis/crisis escalation.

    A deceased person calling/crying inside a dream is treated as grief support unless the user
    expresses a direct urge to go, join, die, or harm themselves.
    """
    t = normalize_for_safety(text)

    dream_patterns = [
        r"\b(friend|mother|father|sister|brother|wife|husband|child|someone)\b.*\bdream\b.*\b(calling me|talking to me|came|appeared|crying)\b",
        r"\bi saw .* in my dream\b",
        r"\bsomeone came in my dream\b",
        r"\bmy dead .* (came|appeared|was there|calling me|crying)\b.*\bdream\b",
        r"\b(dead|died|passed away|deceased)\b.{0,35}\b(calling|crying|talking)\b.{0,35}\b(after waking|woke up|dream)\b",
    ]

    direct_intent_patterns = [
        r"\b(i should|should|want to|need to|have to|will)\b.{0,20}\b(go|join|come)\b.{0,20}\b(her|him|them|dead|friend|mother|father)\b",
        r"\bgo with (her|him|them)\b",
        r"\bjoin (her|him|them)\b",
        r"\b(want to die|kill myself|end my life|suicide)\b",
    ]

    awake_patterns = [
        r"\bi hear .* calling me\b",
        r"\bvoices? .* talking to me\b",
        r"\bshe is telling me to come\b",
        r"\bhe is telling me to come\b",
        r"\btelling me to join\b",
        r"\bsomeone standing in my room\b",
        r"\bwhile awake\b",
        r"\bwhen i am awake\b",
    ]

    if any(re.search(p, t) for p in direct_intent_patterns):
        return {"detected": True, "type": "psychosis_grief_escalation", "level": "crisis"}

    if any(re.search(p, t) for p in dream_patterns):
        return {
            "detected": True,
            "type": "dream_grief",
            "level": "support",
            "response": (
                "That sounds really upsetting, and I am glad you told me. Dreams about someone who has died can feel heavy, especially when your mind has been under a lot of strain. "
                "You do not have to sit with this alone. Would you feel okay talking to someone you trust about how this dream affected you?"
            )
        }

    if any(re.search(p, t) for p in awake_patterns):
        return {"detected": True, "type": "psychosis_or_command", "level": "crisis"}

    return {"detected": False}



def is_grief_dream_without_self_harm(text):
    """Detect grief/disturbing experiences about a deceased person without automatic crisis.

    This is intentionally a safety nuance, not normal response routing. Crisis still wins when
    the user says they want/need/should go, join the deceased person, die, or harm themselves.
    It also treats phrases like "after waking up" as dream context, because users often continue
    the same dream story without repeating the word "dream" every turn.
    """
    t = normalize_for_safety(text or "")

    deceased_context = any(x in t for x in [
        "dead friend", "dead mother", "dead father", "dead sister", "dead brother",
        "my friend died", "friend who died", "passed away", "deceased", "someone who died"
    ]) or ("dead" in t and any(x in t for x in ["friend", "mother", "father", "sister", "brother", "person", "someone"]))

    dream_context = any(x in t for x in [
        "dream", "dreams", "in my dream", "in my dreams", "after waking", "when i woke", "woke up"
    ])

    grief_content = any(x in t for x in [
        "calling me", "crying", "came", "appeared", "saw", "see", "talking to me", "i am scared", "scared"
    ])

    crisis_intent = any(x in t for x in [
        "i should go", "i want to go", "i need to go", "i have to go", "i will go",
        "go to her", "go to him", "join her", "join him", "join them",
        "go with her", "go with him", "want to die", "kill myself", "end my life", "suicide"
    ])

    awake_hallucination_context = any(x in t for x in [
        "while awake", "when i am awake", "i hear voices", "voices are", "voice is", "standing in my room"
    ])

    return bool(deceased_context and grief_content and not crisis_intent and not awake_hallucination_context and dream_context)

def build_grief_dream_support_response(user_text, name="", role="default"):
    """Warm support for grief-related dreams without over-triggering crisis."""
    display_name = (name or "").strip()
    prefix = f"{display_name}, " if display_name else ""

    if os.environ.get("OPENAI_API_KEY"):
        try:
            system_prompt = """
You are Empath AI, a warm mental-health support chatbot for IDPs in Pakistan.
The user is describing a grief-related dream about someone who died. They have NOT expressed wanting to die or join the deceased person.
Respond with emotional support, not emergency panic.
Do not validate the dream as supernatural or real. Do not dismiss it.
Include: gentle validation, one hopeful IDP-sensitive line, a short dua/prayer for the deceased if appropriate, and exactly one simple follow-up question.
Keep it 4-6 sentences, warm and natural.
""".strip()
            prompt = f"User name: {display_name or 'User'}\nRole: {role}\nUser message: {user_text}\nWrite the next response."
            print("Empath AI selected model:", os.environ.get("EMPATH_AI_MODEL", "gpt-4o-mini"))
            result = client.chat.completions.create(
                model=os.environ.get("EMPATH_AI_MODEL", "gpt-4o-mini"),
                
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.75,
                max_tokens=180,
            )
            response = result.choices[0].message.content.strip()
            response = _ensure_one_followup(response, user_text) if '_ensure_one_followup' in globals() else response
            return _with_comfort(response, "psychosis")
        except Exception as e:
            print("Grief dream OpenAI response error:", e)

    options = [
        (
            f"{prefix}seeing your friend like that in a dream sounds really painful and unsettling. "
            "Dreams about someone we have lost can bring sadness back very strongly, especially when the heart is already carrying a lot. "
            "After everything you have been managing, your mind and heart may be asking for gentleness right now. "
            "You can make dua for your friend — may Allah grant them peace, mercy, and light. "
            "How did you feel when you woke up from that dream?"
        ),
        (
            f"{prefix}that dream sounds heavy, especially because it was about someone you cared about. "
            "When grief is still inside us, the mind can bring that person into dreams in emotional ways. "
            "Even with so much change around you, talking about it is a strong step instead of holding it alone. "
            "May Allah give your friend peace and give your heart comfort too. "
            "What part of the dream stayed with you the most?"
        ),
        (
            f"{prefix}I can understand why that would shake you — seeing your friend crying in a dream can feel very real and upsetting. "
            "It may be your grief showing how much you still care about them, not something you have to face alone. "
            "When life feels unsettled, painful memories and dreams can feel even stronger, but sharing them can bring a little ease. "
            "Please make a gentle dua for your friend, and be kind to yourself tonight too. "
            "Would you like to tell me what happened in the dream?"
        ),
    ]
    return _with_comfort(random.choice(options), "psychosis")


def run_safety_checks(user_text):
    normalized = normalize_for_safety(user_text)

    # Grief-related dreams should receive emotional support unless the user expresses
    # a direct wish/plan to die, go with, or join the deceased person.
    if is_grief_dream_without_self_harm(normalized):
        return {"type": "grief_dream", "level": "support"}

    if direct_crisis_override(normalized):
        return {"type": "emergency", "level": "emergency"}

    # Prevent false-positive crisis detection for phrases like "kill this exam/project".
    if is_metaphor_kill(normalized):
        return {"type": "safe", "level": "safe", "metaphor_kill": True}

    guardrail = check_guardrail(normalized)
    if guardrail["blocked"]:
        return {"type": "blocked", "response": guardrail["response"]}

    # Psychosis-like experiences should be detected before general grief-crisis intent,
    # so the response can stay calm, grounding, and clinically safer.
    dream_risk = detect_dream_or_hallucination_risk(normalized)
    if dream_risk["detected"]:
        if dream_risk["level"] == "crisis":
            return {"type": "psychosis", "level": "crisis", "category": dream_risk.get("type", "psychosis_risk")}
        return {"type": "special_support", "level": "support", "response": dream_risk["response"]}

    psychosis = detect_psychosis(normalized)
    if psychosis["psychosis_detected"]:
        level = "emergency" if psychosis["level"] == "EMERGENCY" else "crisis" if psychosis["level"] == "CRISIS" else "distress"
        return {"type": "psychosis", "level": level, "category": psychosis["category"]}

    if has_grief_intent(normalized):
        return {"type": "crisis", "level": "crisis"}

    crisis_level = detect_crisis(normalized)
    if crisis_level == "emergency":
        return {"type": "emergency", "level": "emergency"}
    if crisis_level == "crisis":
        return {"type": "crisis", "level": "crisis"}

    return {"type": "safe", "level": crisis_level}

def get_severity(phq_score):
    if phq_score <= 4:
        return "Minimal"
    elif phq_score <= 9:
        return "Mild"
    elif phq_score <= 14:
        return "Moderate"
    return "Severe"

def select_quran_verse(user_text):
    text = normalize_for_safety(user_text)
    used = session.get("used_verses", [])

    if any(w in text for w in ["suicide", "kill myself", "want to die", "hopeless", "no point"]):
        preferred = "hope"
    elif any(w in text for w in ["burden", "too much", "exhausted", "tired"]):
        preferred = "burden"
    else:
        preferred = "hardship"

    candidates = [v for v in QURAN_HOPE_VERSES if v["topic"] == preferred and v["ref"] not in used]
    if not candidates:
        candidates = [v for v in QURAN_HOPE_VERSES if v["ref"] not in used]
    if not candidates:
        used = []
        session["used_verses"] = used
        candidates = QURAN_HOPE_VERSES[:]

    verse = random.choice(candidates)
    used.append(verse["ref"])
    session["used_verses"] = used
    return verse

def select_psychosis_verse(user_text):
    text = normalize_for_safety(user_text)
    used = session.get("used_psychosis_verses", [])

    if any(w in text for w in ["voices", "talking to me", "standing in my room", "watching me"]):
        preferred = "protection"
    elif any(w in text for w in ["scared", "fear", "frightening", "panic"]):
        preferred = "calm"
    else:
        preferred = "healing"

    candidates = [v for v in PSYCHOSIS_SUPPORT_VERSES if v["topic"] == preferred and v["ref"] not in used]
    if not candidates:
        candidates = [v for v in PSYCHOSIS_SUPPORT_VERSES if v["ref"] not in used]
    if not candidates:
        used = []
        session["used_psychosis_verses"] = used
        candidates = PSYCHOSIS_SUPPORT_VERSES[:]

    verse = random.choice(candidates)
    used.append(verse["ref"])
    session["used_psychosis_verses"] = used
    return verse


def get_religious_support_line(user_text=""):
    text = normalize_for_safety(user_text)

    if any(w in text for w in ["hopeless", "no hope", "nothing left", "end"]):
        return 'Allah says in the Quran: "Do not lose hope in the mercy of Allah." (Quran 39:53)'
    if any(w in text for w in ["burden", "too much", "cannot bear", "heavy"]):
        return 'Allah says in the Quran: "Allah does not burden a soul beyond what it can bear." (Quran 2:286)'
    if any(w in text for w in ["hard", "hardship", "pain", "suffering"]):
        return 'Allah says in the Quran: "Indeed, with hardship comes ease." (Quran 94:5-6)'

    return random.choice([
        'Allah says in the Quran: "Do not lose hope in the mercy of Allah." (Quran 39:53)',
        'Allah says in the Quran: "Indeed, with hardship comes ease." (Quran 94:5-6)',
        'Allah says in the Quran: "Allah does not burden a soul beyond what it can bear." (Quran 2:286)',
        'In Islam, even deep pain is seen by Allah, and a hurting heart is not forgotten.',
        'In our religion, hardship is not the end of a person’s story, and mercy is never closed.'
    ])


# ─────────────────────────────────────────────
# CRISIS MODE (LLM-driven, with hard safety rules)
# ─────────────────────────────────────────────
def user_invited_faith():
    """True only if the user has brought up religion themselves."""
    history = list(session.get("history", []))
    text = " ".join(
        str(m.get("content", "")).lower()
        for m in history if m.get("role") == "user"
    )
    return any(w in text for w in FAITH_WORDS)


def _has_unknown_phone_number(text):
    """Block any phone number the model invented."""
    found = re.findall(r"\b0\d{3}-?\d{7}\b|\b\d{4}-?\d{3}-?\d{3}\b", text or "")
    return any(num not in ALLOWED_PHONE_NUMBERS for num in found)


CRISIS_METHOD_WORDS = [
    "pill", "pills", "tablet", "tablets", "medicine", "overdose", "poison",
    "rope", "knife", "blade", "jump",
    "گولی", "گولیاں", "دوائی", "زہر", "رسی", "چھری",
    "goli", "golian", "goliyan", "dawai", "dawa", "zehar", "zeher", "rassi", "churi", "chhuri",
]


# The user has ALREADY said clearly that they want to die / kill themselves.
# Then the bot must not ask "are you having thoughts of ending your life?" again.
EXPLICIT_INTENT_PATTERNS = [
    r"\bkill (my ?self|me)\b", r"\bwant(ed)? to die\b", r"\bend(ing)? my life\b", r"\bsuicid",
    r"\bdon'?t want to (live|be alive)\b", r"\bdo not want to (live|be alive)\b",
    r"\btake my (own )?life\b", r"\bbetter off dead\b", r"\bend it all\b",
    r"خودکشی", r"مرنا چاہت", r"خود کو (مار|ختم)", r"زندگی ختم کر", r"جینا نہیں چاہت", r"مر جاؤں", r"مر جاوں",
    r"\bkhud ?kushi\b", r"\bmarna chaht", r"\bkhud ko (mar|maar|khatam)", r"\bmar ja(un|oon|aon|wun)\b",
    r"\bzindagi khatam\b", r"\bjeena nahi? chaht",
]


def _states_explicit_intent(text):
    t = (text or "").lower()
    return any(re.search(p, t) for p in EXPLICIT_INTENT_PATTERNS)


def _crisis_reflection_llm(user_text, language, repeat):
    """1-2 warm sentences that reflect what THIS user actually said.

    Only the opening is written by the model. The safety question and the helpline
    lines are added by code, so they can never be dropped or changed.
    Returns None if the model is unavailable or the text fails any check.
    """
    if not os.environ.get("OPENAI_API_KEY") or not (user_text or "").strip():
        return None

    lang_rule = ("Write ONLY in natural Urdu script (no English, no Roman Urdu)."
                 if language == "ur" else "Write ONLY in English.")
    system_prompt = f"""
You are Empath AI, a warm counselling chatbot for displaced people in Pakistan.
The user just said they are thinking about ending their life. Write ONLY the opening
of the reply: 1 or 2 short sentences (max 40 words) that:
- reflect back the specific thing they shared, in your own gentle words
  (e.g. "thoughts that are hurting you from inside", "losing your home", "feeling like a burden"),
- thank them for telling you {"again " if repeat else ""}and say you are here with them.
Rules:
- Do NOT ask any question. Do NOT give advice, phone numbers or helplines (they are added separately).
- Do NOT say the feeling is "natural", "normal" or "understandable to want to die".
- Do NOT mention methods, medicines or how to hurt oneself.
- Do NOT use the user's name, religion, or "I'm sorry to hear".
- {lang_rule}
{URDU_STYLE_RULES if language == "ur" else ""}
""".strip()

    try:
        result = client.chat.completions.create(
            model=os.environ.get("EMPATH_AI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "system", "content": system_prompt},
                      {"role": "user", "content": str(user_text)[:600]}],
            temperature=0.5,
            max_tokens=90,
        )
        text = (result.choices[0].message.content or "").strip().strip('"')
    except Exception as e:
        print("Crisis reflection LLM error:", e)
        return None

    bad = (
        not text or len(text) > 320 or "?" in text or "؟" in text or "[[" in text or "]]" in text
        or re.search(r"\d{3,}", text)
        or _has_unknown_phone_number(text)
        or re.search(r"\b(natural|normal|understandable)\b", text.lower())
        or "فطری" in text or "نارمل" in text
        or any(w in text.lower() for w in CRISIS_METHOD_WORDS)
        or not _reply_language_ok(text, language)
    )
    if bad:
        print("Crisis reflection rejected, using fixed opening:", text[:120])
        return None
    return text


# ---------------------------------------------------------------
# QURAN / HADITH COMFORT LINE
# Added at the END of crisis, hopelessness and psychosis replies.
# Only verses of mercy, nearness and ease. Nothing about sin,
# punishment, tests or "be patient", which can add guilt in a crisis.
# English: Sahih International. Urdu: Fateh Muhammad Jalandhari.
# Please have these checked once by a scholar before release.
# ---------------------------------------------------------------
CRISIS_COMFORT_AYAT = [
    {
        "id": "94:5-6", "psychosis_ok": True,
        "arabic": "فَإِنَّ مَعَ الْعُسْرِ يُسْرًا ۝ إِنَّ مَعَ الْعُسْرِ يُسْرًا",
        "en": "For indeed, with hardship [will be] ease. Indeed, with hardship [will be] ease.",
        "ur": "ہاں ہاں مشکل کے ساتھ آسانی بھی ہے۔ (اور) بےشک مشکل کے ساتھ آسانی ہے۔",
        "ref_en": "Quran, Surah Ash-Sharh 94:5-6", "ref_ur": "قرآن، سورۃ الشرح 94:5-6",
    },
    {
        "id": "13:28", "psychosis_ok": True,
        "arabic": "أَلَا بِذِكْرِ اللَّهِ تَطْمَئِنُّ الْقُلُوبُ",
        "en": "Unquestionably, by the remembrance of Allah hearts are assured.",
        "ur": "سن رکھو کہ اللہ کی یاد سے دل آرام پاتے ہیں۔",
        "ref_en": "Quran, Surah Ar-Ra'd 13:28", "ref_ur": "قرآن، سورۃ الرعد 13:28",
    },
    {
        "id": "9:40", "psychosis_ok": True,
        "arabic": "لَا تَحْزَنْ إِنَّ اللَّهَ مَعَنَا",
        "en": "Do not grieve; indeed Allah is with us.",
        "ur": "غم نہ کرو، اللہ ہمارے ساتھ ہے۔",
        "ref_en": "Quran, Surah At-Tawbah 9:40", "ref_ur": "قرآن، سورۃ التوبہ 9:40",
    },
    {
        "id": "39:53", "psychosis_ok": False,
        "arabic": "لَا تَقْنَطُوا مِن رَّحْمَةِ اللَّهِ",
        "en": "Do not despair of the mercy of Allah.",
        "ur": "اللہ کی رحمت سے ناامید نہ ہونا۔",
        "ref_en": "Quran, Surah Az-Zumar 39:53", "ref_ur": "قرآن، سورۃ الزمر 39:53",
    },
    {
        "id": "93:3", "psychosis_ok": False,
        "arabic": "مَا وَدَّعَكَ رَبُّكَ وَمَا قَلَىٰ",
        "en": "Your Lord has not taken leave of you, nor has He detested [you].",
        "ur": "تمہارے پروردگار نے نہ تو تم کو چھوڑا اور نہ (تم سے) ناراض ہوا۔",
        "ref_en": "Quran, Surah Ad-Duha 93:3", "ref_ur": "قرآن، سورۃ الضحیٰ 93:3",
    },
    {
        "id": "2:186", "psychosis_ok": False,
        "arabic": "وَإِذَا سَأَلَكَ عِبَادِي عَنِّي فَإِنِّي قَرِيبٌ",
        "en": "And when My servants ask you concerning Me, indeed I am near.",
        "ur": "اور جب تم سے میرے بندے میرے بارے میں دریافت کریں تو (کہہ دو کہ) میں تو تمہارے پاس ہوں۔",
        "ref_en": "Quran, Surah Al-Baqarah 2:186", "ref_ur": "قرآن، سورۃ البقرہ 2:186",
    },
]

# Hadith are taken from data/quotes.json (already verified, with sunnah.com links)
CRISIS_COMFORT_HADITH_IDS = {
    "hope_hadith_001": True,       # mercy divided into 100 parts            (psychosis ok)
    "lonely_hadith_001": True,     # believers are like one body             (psychosis ok)
    "sad_hadith_002": True,        # the tender plant bends and rises again  (psychosis ok)
    "hope_hadith_002": False,      # "I am with him when he remembers Me"
}

_HADITH_BOOKS_UR = {
    "Sahih al-Bukhari": "صحیح بخاری", "Sahih Muslim": "صحیح مسلم", "Sunan Abi Dawud": "سنن ابو داؤد",
    "Jami at-Tirmidhi": "جامع ترمذی", "Jami` at-Tirmidhi": "جامع ترمذی",
}

# If the user has said they follow another faith, do not add Islamic texts.
_OTHER_FAITH_RE = re.compile(
    r"\b(christian|church|bible|jesus|yesu|isai|masihi|hindu|bhagwan|mandir|sikh|gurdwara|ahmadi)\b"
    r"|مسیحی|عیسائی|چرچ|ہندو|بھگوان|مندر|سکھ",
    re.IGNORECASE,
)


def _user_follows_other_faith():
    for item in session.get("history", []) or []:
        if item.get("role") == "user" and _OTHER_FAITH_RE.search(str(item.get("content", ""))):
            return True
    return False


def _comfort_hadith_pool(psychosis):
    try:
        from .core import DATA_PATH
        with open(os.path.join(DATA_PATH, "quotes.json"), "r", encoding="utf-8") as fh:
            quotes = {q.get("id"): q for q in json.load(fh) if isinstance(q, dict)}
    except Exception:
        return []
    pool = []
    for qid, psychosis_ok in CRISIS_COMFORT_HADITH_IDS.items():
        if qid in quotes and (psychosis_ok or not psychosis):
            pool.append(quotes[qid])
    return pool


def crisis_comfort_line(kind="crisis"):
    """One gentle ayah or hadith (with translation and source) for the end of a reply.

    kind: "crisis", "hopeless" or "psychosis". Returns "" when nothing should be added.
    Never repeats the same text inside one chat.
    """
    if _user_follows_other_faith():
        return ""
    language = session.get("language", "en")
    psychosis = kind == "psychosis"
    seen = set(session.get("comfort_seen", []))

    options = [("ayah", a) for a in CRISIS_COMFORT_AYAT if a["psychosis_ok"] or not psychosis]
    options += [("hadith", h) for h in _comfort_hadith_pool(psychosis)]
    recent = list(session.get("comfort_recent", []))   # last few shown, in order
    # Never one of the last 3 shown, and nothing repeats until all have been shown once
    fresh = [o for o in options if o[1].get("id") not in seen and o[1].get("id") not in recent[-3:]]
    if not fresh:
        seen = set()
        fresh = [o for o in options if o[1].get("id") not in recent[-3:]] or options
    if not fresh:
        return ""

    kind_of, item = random.choice(fresh)
    seen.add(item.get("id"))
    session["comfort_seen"] = list(seen)
    session["comfort_recent"] = (recent + [item.get("id")])[-3:]

    if kind_of == "ayah":
        if language == "ur":
            return (f"🤍 دل کے سکون کے لیے:\n{item['arabic']}\n"
                    f"\"{item['ur']}\"\n— {item['ref_ur']}")
        return (f"🤍 Something to hold on to:\n{item['arabic']}\n"
                f"\"{item['en']}\"\n— {item['ref_en']}")

    tr = item.get("translations", {})
    t = tr.get("ur" if language == "ur" else "en") or tr.get("en", {})
    source = t.get("title") or tr.get("en", {}).get("title", "")
    if language == "ur":
        for book_en, book_ur in _HADITH_BOOKS_UR.items():
            source = source.replace(book_en, book_ur)
        return f"🤍 دل کے سکون کے لیے:\n\"{t.get('quote', '')}\"\n— نبی کریم ﷺ ({source})"
    return f"🤍 Something to hold on to:\n\"{t.get('quote', '')}\"\n— Prophet Muhammad ﷺ ({source})"


def _with_comfort(reply, kind="crisis"):
    line = crisis_comfort_line(kind)
    return f"{reply.rstrip()}\n\n{line}" if line else reply


# ---------------------------------------------------------------
# Helpline (Umang / 1122) is shown ONCE per chat. After that the bot
# just listens like a friend. Only a new, direct statement of wanting
# to die gets a one-line reminder. "New Chat" / logout resets this.
# ---------------------------------------------------------------
def helpline_already_shared():
    return bool(session.get("helpline_shared"))


def mark_helpline_shared():
    session["helpline_shared"] = True


def build_crisis_response(user_text, name=""):
    """Safety response when risk is detected.

    The opening reflects what the user said (short LLM line, checked; fixed text as backup).
    The safety question and helplines are always fixed text added by code.

    - First time in this conversation: full safety message.
    - Risk comes up again: a different opening, so the user feels heard, not looped.
    - A method (pills, poison...) is mentioned: refuse details and ask about access.
    """
    repeat = session.get("crisis_step") in ("initial", "followup", "repeat")
    text_l = (user_text or "").lower()
    mentions_method = any(w in text_l for w in CRISIS_METHOD_WORDS)
    explicit = _states_explicit_intent(user_text)

    session["crisis_step"] = "repeat" if repeat else "initial"
    session["risk_mentioned"] = True
    session["crisis_safe_turns"] = 0
    language = session.get("language", "en")
    first_name = (name or "").strip().split(" ")[0] if name else ""

    if language == "ur":
        # Use the name only if it is already in Urdu script
        ur_name = first_name if re.search(r"[\u0600-\u06FF]", first_name) else ""
        if not ur_name:
            try:
                from .database import get_urdu_name_for_account
                saved = (get_urdu_name_for_account(session.get("account_id")) or "").strip()
                ur_name = saved.split(" ")[0] if re.search(r"[\u0600-\u06FF]", saved) else ""
            except Exception:
                ur_name = ""
        lead = f"{ur_name}، " if ur_name else ""

        if repeat:
            opening = (
                f"{lead}مجھے دوبارہ بتانے کا شکریہ۔ میں آپ کی بات پوری سنجیدگی سے سن رہی ہوں، "
                "اور میں کہیں نہیں جا رہی، یہیں آپ کے ساتھ ہوں۔"
            )
        else:
            opening = (
                f"{lead}مجھ پر بھروسہ کر کے یہ بات بتانے کا شکریہ۔ "
                "میں محسوس کر سکتی ہوں کہ اس وقت آپ کتنی تکلیف میں ہیں، اور میں کہیں نہیں جا رہی۔"
            )

        reflection = _crisis_reflection_llm(user_text, "ur", repeat)
        if reflection:
            opening = f"{lead}{reflection}"

        if explicit:
            safety_q = (
                "آپ نے مجھے صاف بتایا ہے، اس لیے میں سیدھا پوچھنا چاہتی ہوں: کیا آپ اس وقت محفوظ ہیں، "
                "اور کیا کوئی آپ کے ساتھ ہے؟ کیا آپ کے پاس کوئی ایسی چیز ہے جس سے آپ خود کو نقصان پہنچا سکیں؟"
            )
        else:
            safety_q = "میں یقین کرنا چاہتی ہوں کہ آپ محفوظ ہیں۔ کیا آپ کے ذہن میں اپنی زندگی ختم کرنے کے خیالات آ رہے ہیں؟"

        if mentions_method:
            middle = (
                "میں آپ کی حفاظت کی خاطر دوائیوں یا ایسی چیزوں کی تفصیل نہیں بتا سکتی۔ "
                f"{safety_q} "
                "اگر آپ کے پاس گولیاں یا کوئی ایسی چیز ہے جس سے نقصان ہو سکتا ہے، تو براہ کرم اسے خود سے دور رکھ دیں "
                "یا کسی قابلِ اعتماد شخص کو دے دیں۔"
            )
        else:
            middle = safety_q if explicit else f"{safety_q} کیا آپ کے قریب کوئی ہے جو اس وقت آپ کے ساتھ رہ سکے؟"

        if not helpline_already_shared():
            closing = (
                f"آپ کو یہ سب اکیلے نہیں سہنا۔ اُمنگ مفت ہے اور 24 گھنٹے دستیاب ہے: {UMANG_NUMBER}۔ "
                f"اگر آپ کو لگے کہ آپ ان خیالات پر عمل کر سکتے ہیں تو براہ کرم ابھی {EMERGENCY_NUMBER} پر کال کریں۔"
            )
            mark_helpline_shared()
        elif explicit:
            # Already shared once in this chat: only a one-line reminder, because the risk is direct
            closing = f"اگر آپ کو لگے کہ آپ ان خیالات پر عمل کر سکتے ہیں تو ابھی {EMERGENCY_NUMBER} یا اُمنگ پر کال کریں۔"
        else:
            closing = ""
        msg = f"{opening}\n\n{middle}" + (f"\n\n{closing}" if closing else "")
        return _with_comfort(msg, "crisis")

    lead = f"{first_name}, " if first_name else ""
    if repeat:
        opening = (
            f"{lead}thank you for telling me again. I'm listening, and I'm taking this seriously. "
            "I'm still right here with you."
        )
    else:
        opening = (
            f"{lead}thank you for trusting me with this. I can hear how much pain you're in right now, "
            "and I'm not going anywhere."
        )
    reflection = _crisis_reflection_llm(user_text, "en", repeat)
    if reflection:
        if lead and not reflection.startswith(("I ", "I'")):
            reflection = reflection[0].lower() + reflection[1:]
        opening = f"{lead}{reflection}"
    if not lead:
        opening = opening[0].upper() + opening[1:]

    if explicit:
        safety_q = (
            "Because you've told me this so clearly, I want to ask you directly: are you safe right now, "
            "and is anyone with you? Is there anything near you that you could use to hurt yourself?"
        )
    else:
        safety_q = "I want to make sure you're safe. Are you having thoughts of ending your life?"

    if mentions_method:
        middle = (
            "I can't share details about medicines or ways to get hurt, because your safety matters to me. "
            f"{safety_q} "
            "If you have pills or anything you could hurt yourself with nearby, please put them away "
            "or give them to someone you trust."
        )
    else:
        middle = safety_q if explicit else f"{safety_q} Is someone nearby who can be with you?"

    if not helpline_already_shared():
        closing = (
            f"You don't have to go through this alone. Umang is free and there 24/7: {UMANG_NUMBER}. "
            f"If you feel you might act on these thoughts, please call {EMERGENCY_NUMBER} now."
        )
        mark_helpline_shared()
    elif explicit:
        # Already shared once in this chat: only a one-line reminder, because the risk is direct
        closing = f"If you feel you might act on these thoughts, please call {EMERGENCY_NUMBER} or Umang right now."
    else:
        closing = ""
    msg = f"{opening}\n\n{middle}" + (f"\n\n{closing}" if closing else "")
    return _with_comfort(msg, "crisis")


def _crisis_risk_signal(text):
    """Does THIS message still show active risk?"""
    norm = normalize_for_safety(text or "")
    if direct_crisis_override(norm):
        return True
    try:
        return detect_crisis(norm) in ("crisis", "emergency")
    except Exception:
        return False


def _generate_crisis_followup_llm(user_message, exiting, hopeless=False):
    if not os.environ.get("OPENAI_API_KEY"):
        return None

    language = session.get("language", "en")
    faith_ok = user_invited_faith()

    if exiting:
        stage_rule = (
            "The user now seems steadier. Warmly acknowledge this positive shift. "
            "If relevant, give ONE small tip for tonight. Gently say they can come back anytime. "
            "Do NOT write any phone number or helpline name; one is added separately."
        )
    elif hopeless:
        stage_rule = (
            "The user sounds HOPELESS (they feel nothing will get better / they want to give up). "
            "Reflect their exhaustion and effort in their own words, and do NOT jump to tips or advice. "
            "Then gently and directly check on their safety with ONE question, e.g. "
            "'When things feel this hopeless, do thoughts of ending your life come back?' "
            "Do NOT write any phone number or helpline name; one is added separately."
        )
    else:
        stage_rule = (
            "Safety mode is still active but this message shows no new risk. "
            "Be a warm, understanding friend: listen and reflect what they feel. "
            "Do NOT mention helplines, Umang, 1122 or any phone number in this reply "
            "(they were already given; repeating them every time feels robotic)."
        )

    faith_rule = (
        "The user has spoken about faith, so you MAY add one short, gentle faith line if it fits naturally."
        if faith_ok else
        "Do NOT add Quran verses or religious lines. The user has not brought up religion."
    )

    language_rule = (
        "Write ONLY in natural Urdu script. No English, no Roman Urdu."
        if language == "ur" else
        "Write ONLY in English."
    )

    system_prompt = f"""
You are Empath AI, a counselling chatbot for internally displaced people (IDPs) in Pakistan.
Earlier in THIS conversation the user expressed suicidal thoughts, so you are in safety mode.
Their latest message does not contain a new direct risk statement, but stay attentive.

How to respond:
- If the user's latest message ANSWERS your safety question (for example they say they will not
  hurt themselves, they are safe, or someone is with them), FIRST acknowledge that answer warmly
  in one short sentence (for example "I'm relieved to hear you won't act on these thoughts.").
- Respond to what the user ACTUALLY said in their latest message. Name their real feeling
  (anger, fear, flashbacks, grief, relief, exhaustion...). Never assume they are alone,
  sad, or anything they did not say.
- Warm, calm, simple language. 3 to 5 sentences, split into 2 or 3 short paragraphs.
- Validate the pain, but NEVER call the wish to die "natural" or "normal".
- Do not ask "have you ever tried..." questions; gently offer an idea instead.
- Talk like a caring, understanding friend: mostly listen, reflect and make them feel understood.
  Advice is optional. Only if it would clearly help, gently offer ONE small step that fits
  (start with "maybe" / "if you like"), never a list of tips:
  flashbacks/panic -> 5-4-3-2-1 grounding or breathe in 4, hold 4, out 6;
  anger -> validate it, then a safe release (fast walk, squeeze a cloth, write it down);
  sleep problems -> a small night routine.
- When relevant, briefly normalise trauma reactions (flashbacks, fast heartbeat, poor sleep
  after a disaster are common and not a sign of weakness).
- Do not defend or criticise the government, NGOs or any political party.
- {stage_rule}
- {faith_rule}
- Do not use the user's name. Do not start with "I'm sorry" or "It sounds like".
- Ask at most ONE question, only if it helps. Never ask something the user already answered.
- Never give medicine names, doses, or any information about methods of self-harm.
- Never invent phone numbers. The only numbers you may use are {UMANG_NUMBER} and {EMERGENCY_NUMBER}.
- The user may write in English, Urdu script or Roman Urdu. Understand all of them,
  but reply only in the language required below.
- {language_rule}

{(URDU_STYLE_RULES + chr(10) + chr(10) + URDU_EXAMPLES) if language == "ur" else ""}
""".strip()

    messages = [{"role": "system", "content": system_prompt}]
    history = list(session.get("history", []))[-8:]
    for item in history:
        if item.get("role") in ("user", "assistant") and item.get("content"):
            messages.append({"role": item["role"], "content": str(item["content"])})

    if not history or history[-1].get("content") != user_message:
        messages.append({"role": "user", "content": user_message})

    try:
        result = client.chat.completions.create(
            model=os.environ.get("EMPATH_AI_MODEL", "gpt-4o-mini"),
            messages=messages,
            temperature=0.6,
            max_tokens=230,
        )
        reply = (result.choices[0].message.content or "").strip()

        if not reply or _has_unknown_phone_number(reply):
            print("Crisis LLM reply rejected (empty or unknown phone number).")
            return None

        if not _reply_language_ok(reply, language):
            print("Crisis LLM reply rejected (wrong language). Using fallback.")
            return None

        if language == "ur":
            # The user's message is already in history, so the reply before it is [-2]
            previous = ""
            assistant_msgs = [m for m in session.get("history", []) if m.get("role") == "assistant"]
            if assistant_msgs:
                previous = str(assistant_msgs[-1].get("content", ""))
            reply = _polish_urdu_reply(reply, previous)
        else:
            reply = _polish_english_reply(reply, talk_guard=False)

        return reply

    except Exception as e:
        print("Crisis follow-up LLM error:", e)
        return None


def _crisis_followup_fallback(exiting, hopeless=False):
    """Used only if the API fails."""
    language = session.get("language", "en")

    if hopeless:
        if language == "ur":
            return (
                "آپ نے اتنی محنت کی، اور بار بار وہیں واپس پہنچ جانا واقعی بہت تھکا دینے والا ہے۔ "
                "میں آپ کی بات سن رہی ہوں۔" + "\n\n" +
                "جب سب کچھ اتنا ناامید لگے، تو کیا اپنی زندگی ختم کرنے کے خیالات دوبارہ آتے ہیں؟"
            )
        return (
            "You've worked so hard, and ending up back where you started again and again is truly exhausting. "
            "I hear you." + "\n\n" +
            "When things feel this hopeless, do thoughts of ending your life come back?"
        )

    if language == "ur":
        if exiting:
            return (
                "یہ سن کر اچھا لگا کہ اب آپ کچھ بہتر محسوس کر رہے ہیں۔ آج رات اپنے ساتھ نرمی رکھیں، "
                "تھوڑا پانی پئیں اور چند لمبی سانسیں لیں۔ آپ جب چاہیں دوبارہ بات کر سکتے ہیں، "
                f"اور اگر بھاری خیالات واپس آئیں تو اُمنگ {UMANG_NUMBER} پر 24 گھنٹے دستیاب ہے۔"
            )
        return (
            "بات جاری رکھنے کا شکریہ۔ جو کچھ آپ نے جھیلا ہے، اس کے بعد ایسا محسوس ہونا سمجھ میں آتا ہے۔ "
            "آئیں ایک چھوٹی سی چیز کریں: چار تک گن کر سانس اندر لیں، چار تک روکیں، اور چھ تک گن کر آہستہ باہر نکالیں۔"
        )

    if exiting:
        return (
            "I'm glad things feel a little steadier right now. Be gentle with yourself tonight: "
            "a glass of water, a dim light, and a few slow breaths can help your body settle. "
            "You can come back and talk anytime, and Umang "
            f"({UMANG_NUMBER}) is available 24/7 if the heavy thoughts return."
        )
    return (
        "Thank you for staying and telling me this. What you're feeling makes sense after "
        "everything you've been through. Let's try one small thing: breathe in for 4, hold for 4, "
        "and breathe out slowly for 6, three times."
    )


HOPELESS_PATTERNS = [
    r"\bhopeless", r"\bno hope\b", r"\blost (all )?hope\b", r"\bnothing (is going to|will|ever) (get|be) better",
    r"\bnever (going to )?get better", r"\bwon'?t (ever )?get better", r"\bwhat'?s the point\b", r"\bno point\b",
    r"\bnothing matters\b", r"\bi (want to |just want to )?give up\b", r"\bgiving up\b", r"\bcan'?t go on\b",
    r"\bcan'?t take (it|this) anymore\b", r"\bback (to )?where i started\b", r"\bno way out\b", r"\btrapped\b",
    r"\bkoi (umeed|umid|ummeed)\b.*\bnahi", r"\b(umeed|umid) khatam\b", r"\bkuch (theek|thik|behtar) nahi? hoga\b",
    r"\bhaar (gaya|gayi|gai|maan)", r"کوئی امید نہیں", r"امید ختم", r"کچھ ٹھیک نہیں ہوگا", r"کچھ بہتر نہیں ہوگا",
    r"ہار مان", r"ہار گئی", r"ہار گیا", r"کوئی راستہ نہیں",
]


def is_hopeless_message(text):
    t = (text or "").lower().replace("’", "'")
    return any(re.search(p, t) for p in HOPELESS_PATTERNS)


def _strip_helpline_lines(reply):
    """Remove helpline sentences the model added on its own (code adds them when needed)."""
    helpline_sentence = re.compile(
        r"[^.!?۔\n]*(" + re.escape(UMANG_NUMBER) + r"|\b1122\b|\bumang\b|اُمنگ|امنگ|helpline|ہیلپ لائن)[^.!?۔\n]*[.!?۔]?",
        re.IGNORECASE,
    )
    cleaned = helpline_sentence.sub("", reply or "")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned or reply


def _hopeless_helpline(language):
    if language == "ur":
        return (f"اگر ایسے خیالات آ رہے ہیں تو آپ اکیلے نہیں: اُمنگ {UMANG_NUMBER} پر مفت، 24 گھنٹے بات ہو سکتی ہے، "
                f"اور فوری خطرے میں {EMERGENCY_NUMBER} پر کال کریں۔")
    return (f"If those thoughts are there, you don't have to face them alone: Umang ({UMANG_NUMBER}) is free and "
            f"there 24/7, and {EMERGENCY_NUMBER} is there in an emergency.")


def _hopelessness_reply(user_message):
    session["risk_mentioned"] = True
    reply = _generate_crisis_followup_llm(user_message, exiting=False, hopeless=True) \
        or _crisis_followup_fallback(False, hopeless=True)
    reply = _strip_helpline_lines(reply)
    if not helpline_already_shared():
        mark_helpline_shared()
        reply = reply.rstrip() + "\n\n" + _hopeless_helpline(session.get("language", "en"))
    return _with_comfort(reply, "hopeless")


def get_hopelessness_check_response(name, user_message):
    """Hopeless message in normal chat: gentle safety check, then safety mode for a few turns."""
    session["crisis_active"] = True
    session["crisis_level"] = "crisis"
    session["crisis_step"] = "followup"
    session["crisis_safe_turns"] = 0
    return _hopelessness_reply(user_message)


def get_crisis_followup_response(name, user_message):
    """Called for every message while crisis_active is True."""
    text = normalize_for_safety(user_message or "")

    # 1. New risk signal -> full safety message again, reset the calm counter
    if _crisis_risk_signal(user_message):
        session["crisis_active"] = True
        return build_crisis_response(user_message, name)

    # 2. Deep hopelessness -> gentle safety check + helpline, and the calm counter starts again
    if is_hopeless_message(user_message):
        session["crisis_active"] = True
        session["crisis_step"] = "followup"
        session["crisis_safe_turns"] = 0
        return _hopelessness_reply(user_message)

    # 3. Count calm turns. A clear safety confirmation exits immediately.
    if any(p in text for p in SAFETY_CONFIRM_PHRASES):
        session["crisis_safe_turns"] = CRISIS_EXIT_SAFE_TURNS
    else:
        session["crisis_safe_turns"] = session.get("crisis_safe_turns", 0) + 1

    exiting = session["crisis_safe_turns"] >= CRISIS_EXIT_SAFE_TURNS

    # 4. Real, context-aware reply from the LLM (fallback if API fails)
    reply = _generate_crisis_followup_llm(user_message, exiting) or _crisis_followup_fallback(exiting)

    # Helpline: added by code only when leaving safety mode. In the calm turns in
    # between it is NOT repeated, because it was already given in the safety message.
    reply = _strip_helpline_lines(reply)
    if exiting and not helpline_already_shared():
        reply = reply.rstrip() + "\n\n" + _umang_reminder(session.get("language", "en"))
        mark_helpline_shared()

    reply = _with_comfort(reply, "crisis")

    # 5. Update state
    if exiting:
        session["crisis_active"] = False
        session["crisis_level"] = "safe"
        session["crisis_step"] = ""
        session["crisis_safe_turns"] = 0
    else:
        session["crisis_active"] = True
        session["crisis_step"] = "followup"

    return reply

# ---------------------------------------------------------------
# UNUSUAL EXPERIENCES: seeing dead people, sensing someone in the room
# Could be nightmares, grief, trauma or psychosis. The bot never confirms,
# denies or "explains" the experience. It stays calm, does not judge,
# and asks the questions that matter. If such experiences keep coming up
# in one chat (dream -> after waking -> awake), each reply takes it more
# seriously and suggests seeing a doctor.
# Helpline only if risk came up earlier in this chat AND was not shared yet.
# ---------------------------------------------------------------
def _strip_madda(text):
    """Voice typing often writes آ as ا ("نظر اتے"): match both."""
    return (text or "").replace("آ", "ا").replace("ٔ", "")


SEES_DECEASED_PATTERNS = [
    # Roman Urdu: "mare hue / mary wy / mary vy / marey huay log nazar aate hain", "murday dikhte hain"
    r"\b(mare|mary|marey|maray|mrey|mre|mry|mar)\s*"
    r"(hue|huay|huey|huye|hui|hoye|hoay|hoey|hoe|hoy|hoi|huwe|hwe|hwy|wy|wi|we|way|wey|vy|vi|ve|vay|vey|vye|hovay|hovey|huvay|hue?y)\b"
    r".{0,30}\b(nazar|nzr|dikh|dikhai|dikhay|dekhti|dekhta|dikhte|dikhty)",
    r"\b(nazar|nzr|dikh\w*)\b.{0,20}\b(mare|mary|marey|mry)\s*(hue|huay|huye|hoye|wy|vy|we|way)\b",
    r"\b(murd[ae]y?|murdy|murday|murde|murdon|faut shuda|fot shuda|fout shuda|wafat shuda|faut (ho )?chuk[ey]|fot (ho )?chuk[ey])\b"
    r".{0,30}\b(nazar|nzr|dikh|dikhai|dikhay|dikhte|dikhty)",
    # Urdu script (written without madda, text is normalised the same way)
    r"(مرے ہوئے|مرے ہوے|مرے ہووے|فوت شدہ|فوت ہو چکے|فوت ہوئے|وفات پا چکے|مردہ|مردے|مرحوم).{0,30}(نظر ات|دکھائی دیت|دکھتے|دکھتی|دیکھتی|دیکھتا)",
    r"(نظر ات|دکھائی دیت|دکھتے|دکھتی).{0,25}(مرے ہوئے|مرے ہوے|مردے|فوت شدہ)",
    # English (present / repeated, not a past memory such as "I saw bodies in the flood")
    r"\b(i see|i keep seeing|i still see|i can see|seeing)\b.{0,20}\b(dead (people|person|relatives?)|people who (have )?died|ghosts?|spirits?)\b",
    r"\b(dead (people|person|relatives?)|people who (have )?died)\b.{0,25}\b(appear|keep appearing|come to me|visit me)\b",
]

# Feeling someone is there when nobody is (awake / just after waking)
PRESENCE_PATTERNS = [
    r"(کوئی|کچھ لوگ|لوگ|وہ لوگ).{0,20}(میرے )?(پیچھے|کمرے میں|پاس|ساتھ).{0,15}(کھڑا|کھڑی|کھڑے|موجود|بیٹھا|بیٹھے)",
    r"(کمرے میں).{0,25}(کوئی|لوگ).{0,15}(ہے|ہیں|موجود)",
    r"حالانکہ.{0,15}(وہاں )?کوئی (نہیں|نہ)",
    r"\b(koi|log|wo log)\b.{0,20}\b(peeche|pichay|peechay|kamre mein|kamray mein|kamre me)\b.{0,15}\b(khara|khari|khare|mojood|maujood|baitha)",
    r"\bhalanke\b.{0,15}\bkoi (nahi|nai)\b",
    r"\bsomeone (is )?(standing )?(behind me|in my room|next to my bed|watching me sleep)\b",
    r"\b(feel|sense|felt)\b.{0,20}\b(someone|a presence|people)\b.{0,20}\b(behind me|in (my|the) room|next to me)\b",
    r"\b(but|though|even though) (there'?s|there is|there was) no ?one (there|in the room)\b",
    r"\b(feel|felt|feels) (like )?(they|people|someone|somebody) (are|is|were|was) (still )?(here|there|in my room|in the room|around me|behind me)\b",
    r"\b(they|people) (are|were) (still )?in my room\b",
]

# Follow-up messages in the same story ("they are not my family, just random dead people")
PERCEPTION_FOLLOWUP_RE = (
    r"dead people|dead person|strangers? who (have )?died|مرے ہوئے|مرے ہوے|مردے|فوت شدہ"
    r"|\b(mare|mary|marey|mry)\s*(hue|huay|huye|hoye|wy|vy|we|way)\b|\bmurd"
)

_AWAKE_RE = r"جاگت|جاگنے کے بعد|جاگ کر|اٹھنے کے بعد|\b(jagte|jaagte|jag kar|jaag kar|uthne ke baad|awake|after (i )?wak(e|ing)|when i wake)\b"
_DREAM_RE = r"خواب|\b(khwab|khawab|khaab|dream|dreams|nightmare|nightmares)\b"

_DECEASED_CALLING_RE = re.compile(
    r"\b(call(ing)?|join|come with|take me|go to (him|her|them))\b|\b(bula|bulate|bulati|bulaty|sath le ja|paas bula)"
    r"|بلا(تے|تی|رہے|رہی)|ساتھ لے جا|پاس بلا|ان کے پاس جانا",
    re.IGNORECASE,
)


def _matches_any(patterns, text):
    t = _strip_madda((text or "").lower())
    return any(re.search(_strip_madda(p), t) for p in patterns)


def sees_deceased(text):
    return _matches_any(SEES_DECEASED_PATTERNS, text)


def senses_presence(text):
    return _matches_any(PRESENCE_PATTERNS, text)


def _is_perception_followup(text):
    """Same story continued: earlier in this chat the user described such experiences."""
    if not session.get("perception_seen"):
        return False
    t = _strip_madda((text or "").lower())
    return bool(re.search(_strip_madda(PERCEPTION_FOLLOWUP_RE), t))


def _perception_kind(text):
    t = _strip_madda((text or "").lower())
    awake = bool(re.search(_strip_madda(_AWAKE_RE), t))
    dream = bool(re.search(_DREAM_RE, t))
    if senses_presence(text):
        return "presence"
    if sees_deceased(text):
        return "dream" if dream and not awake else "deceased"
    if _is_perception_followup(text):
        seen = session.get("perception_seen", {}) or {}
        if awake:
            return "deceased"
        return "dream" if seen.get("dream") else "deceased"
    return None


def build_perception_response(user_text, kind):
    """Reply for unusual experiences. kind: 'dream', 'deceased' or 'presence'.

    The reply changes as the story develops in the same chat:
    first time -> understand & ask; again -> notice the change & take it more seriously.
    """
    language = session.get("language", "en")
    ur = language == "ur"
    t = _strip_madda((user_text or "").lower())
    earlier_risk = bool(session.get("risk_mentioned"))

    seen = dict(session.get("perception_seen", {}) or {})
    total_before = sum(seen.values())
    nth = seen.get(kind, 0) + 1          # how many times THIS kind came up
    seen[kind] = nth
    session["perception_seen"] = seen
    total = total_before + 1               # all unusual experiences in this chat

    not_family = bool(re.search(r"قریبی.{0,25}(نہیں|نہ)|جاننے والے نہیں|اپنے نہیں|\b(not (my )?(family|relatives|loved ones)|strangers?|random)\b"
                                r"|\b(apne|qareebi|rishtedar)\b.{0,15}\b(nahi|nai)\b", t))
    room = bool(re.search(r"کمرے|\b(kamre|kamray|room)\b", t))
    behind = bool(re.search(r"پیچھے|\b(peeche|pichay|peechay|behind)\b", t))
    awake_now = bool(re.search(r"جاگتے ہوئے|\b(jagte|jaagte|while awake|when i'?m awake|even when i'?m awake)\b", t))
    after_waking = bool(re.search(r"جاگنے کے بعد|اٹھنے کے بعد|\b(after (i )?wak(e|ing)|uthne ke baad|jagne ke baad)\b", t))

    def pick(en, urdu):
        return urdu if ur else en

    parts = []

    if kind == "dream":
        if nth == 1:
            parts.append(pick(
                "Having nightmares where you see people who have died sounds really frightening and draining. Thank you for telling me.",
                "ایسے خواب آنا، جن میں مرے ہوئے لوگ نظر آئیں، واقعی ڈراؤنا اور تھکا دینے والا ہوتا ہے۔ مجھے بتانے کا شکریہ۔"))
            parts.append(pick(
                "How long have these dreams been happening, and how is your sleep because of them?",
                "یہ خواب کب سے آ رہے ہیں، اور ان کی وجہ سے آپ کی نیند کیسی ہو رہی ہے؟"))
        else:
            parts.append(pick(
                ("I understand, so these aren't people you know, just strangers who have died. " if not_family else "") +
                "Seeing faces like that again and again in your dreams can leave you uneasy even during the day.",
                ("سمجھ گئی، یعنی یہ آپ کے جاننے والے نہیں بلکہ انجان لوگ ہوتے ہیں۔ " if not_family else "") +
                "ایسے چہرے بار بار خواب میں آنا دن میں بھی دل کو بے چین رکھ سکتا ہے۔"))
            parts.append(pick(
                "Do these dreams come almost every night? And has it started to make you afraid of going to sleep?",
                "کیا یہ خواب تقریباً روز آتے ہیں؟ اور کیا اب آپ کو سونے سے بھی ڈر لگنے لگا ہے؟"))

    elif kind == "deceased":
        parts.append(pick(
            "That sounds like a really unsettling experience, and I'm glad you told me. I want to understand it properly, and I won't judge you.",
            "یہ سن کر مجھے احساس ہو رہا ہے کہ آپ کچھ بہت پریشان کن تجربات سے گزر رہے ہیں۔ میں آپ کی بات کو سنجیدگی سے سمجھنا چاہتی ہوں، اور آپ کو جج نہیں کروں گی۔"))
        parts.append(pick(
            "Do you see them only in your dreams, or also when you're awake? Does it frighten you, and do you feel safe right now?",
            "کیا یہ لوگ آپ کو صرف خواب میں نظر آتے ہیں، یا جاگتے ہوئے بھی کبھی اپنے سامنے دیکھتے ہیں؟ کیا یہ تجربہ آپ کو خوفزدہ کرتا ہے، اور کیا اس وقت آپ خود کو محفوظ محسوس کر رہے ہیں؟"))

    else:  # presence
        where_en = "behind you" if behind else "in your room" if room else "near you"
        where_ur = "آپ کے پیچھے" if behind else "آپ کے کمرے میں" if room else "آپ کے پاس"
        if nth == 1 and not (awake_now and total_before >= 1):
            when_en = "after waking up" if after_waking else "while you're awake" if awake_now else ""
            when_ur = "جاگنے کے بعد بھی" if after_waking else "جاگتے ہوئے" if awake_now else ""
            parts.append(pick(
                f"Feeling {when_en + ' ' if when_en else ''}that someone is {where_en}, when nobody is there, must be really frightening. "
                "I'm taking what you say seriously, and I won't judge you.",
                f"{when_ur + ' ' if when_ur else ''}یہ محسوس ہونا کہ کوئی {where_ur} موجود ہے، جبکہ وہاں کوئی نہ ہو، واقعی بہت ڈرا دینے والا ہوگا۔ "
                "میں آپ کی بات کو سنجیدگی سے سن رہی ہوں، اور آپ کو جج نہیں کروں گی۔"))
            parts.append(pick(
                "How often does this happen? And do you feel safe right now?",
                "یہ احساس کتنی بار ہوتا ہے؟ اور کیا اس وقت آپ خود کو محفوظ محسوس کر رہے ہیں؟"))
        else:
            parts.append(pick(
                f"First in your dreams, then after waking, and now even while you're awake, feeling someone {where_en}. "
                "I can hear this is getting heavier for you, and I'm taking it seriously.",
                f"پہلے خواب، پھر جاگنے کے بعد، اور اب جاگتے ہوئے بھی یہ محسوس ہونا کہ کوئی {where_ur} ہے۔ "
                "میں سمجھ سکتی ہوں کہ یہ آپ کے لیے بڑھتا جا رہا ہے، اور میں اسے سنجیدگی سے لے رہی ہوں۔"))
            parts.append(pick(
                "Right now, are you somewhere safe, with someone you trust nearby?",
                "اس وقت کیا آپ کسی محفوظ جگہ پر ہیں، اور کیا کوئی قابلِ اعتماد شخص آپ کے قریب ہے؟"))

    # Getting more serious when it keeps coming up
    if kind != "dream" and total >= 2:
        parts.append(pick(
            "Since this keeps happening, I'd really encourage you to see a doctor soon. "
            "Experiences like this can be treated, and they are not a sign of weakness.",
            "چونکہ یہ بار بار ہو رہا ہے، میرا مشورہ ہے کہ آپ جلد کسی ڈاکٹر سے ملیں۔ "
            "ایسے تجربات کا علاج ممکن ہے، اور یہ آپ کی کمزوری نہیں ہے۔"))

    add_helpline = earlier_risk and not helpline_already_shared() and kind != "dream"
    if earlier_risk and kind != "dream":
        line = pick(
            "Because you mentioned thoughts of hurting yourself earlier, I'd really like you not to be alone right now. "
            "Please ask someone you trust to stay with you.",
            "چونکہ آپ نے پہلے خود کو نقصان پہنچانے کے خیالات کا بھی ذکر کیا تھا، میں چاہتی ہوں کہ آپ اس وقت اکیلے نہ رہیں۔ "
            "کسی قابلِ اعتماد شخص کو اپنے پاس بلائیں۔")
        if add_helpline:
            line += pick(
                f" If you feel you might hurt yourself, call {EMERGENCY_NUMBER} or go to the nearest hospital right away.",
                f" اگر خود کو نقصان پہنچانے کا خطرہ لگے تو فوراً {EMERGENCY_NUMBER} پر رابطہ کریں یا قریبی ہسپتال جائیں۔")
        parts.append(line)
    elif kind == "deceased" and nth == 1:
        parts.append(pick(
            "If it ever feels like they are calling you to come with them, or you get thoughts of hurting yourself, "
            "please tell someone close to you straight away.",
            "اگر کبھی ایسا لگے کہ وہ آپ کو اپنے پاس بلا رہے ہیں، یا خود کو نقصان پہنچانے کا خیال آئے، تو فوراً اپنے کسی قریبی کو بتائیں۔"))

    if nth == 1 or kind != "dream":
        parts.append(pick(
            "What you're going through matters, and you don't have to carry it alone.",
            "آپ کی بات اہم ہے، اور آپ کو یہ سب اکیلے برداشت کرنے کی ضرورت نہیں۔"))

    if add_helpline:
        mark_helpline_shared()
    reply = "\n\n".join(parts)

    # Quran/Hadith line in every such reply (dreams of dead people included)
    return _with_comfort(reply, "psychosis")


def build_seeing_deceased_response(user_text):
    """Kept for older calls."""
    return build_perception_response(user_text, _perception_kind(user_text) or "deceased")


def _build_psychosis_support_response_ur(user_text):
    """Urdu version: calm, does not argue with or confirm the experience."""
    t = (user_text or "").lower()
    severe = re.search(r"مار|ختم|خودکشی|جان لے|نقصان|maar|khatam|khud ?kushi|jaan|nuqsan|kill|harm|die", t)

    if _DECEASED_CALLING_RE.search(t):
        opening = "اپنے کسی پیارے کو کھونے کے بعد ایسا محسوس ہونا بہت دردناک اور الجھا دینے والا ہوتا ہے۔ میں یہیں آپ کے ساتھ ہوں۔"
        support = ("یہ احساس کتنا ہی حقیقی لگے، براہ کرم اس پر عمل نہ کریں۔ اس وقت جہاں ہیں وہیں رہیں اور کسی محفوظ شخص کے قریب رہیں۔ "
                   "کیا آپ کے دل میں ان کے پاس جانے یا خود کو نقصان پہنچانے کا خیال آ رہا ہے؟")
        session["risk_mentioned"] = True
        if not helpline_already_shared():
            mark_helpline_shared()
            help_line = (f"ابھی اپنے قریب کسی شخص کو بتائیں۔ اگر یہ خیال زور پکڑے تو فوراً {EMERGENCY_NUMBER} "
                         f"یا اُمنگ {UMANG_NUMBER} (مفت، 24 گھنٹے) پر کال کریں۔")
        else:
            help_line = f"ابھی اپنے قریب کسی شخص کو بتائیں، اور اگر یہ خیال زور پکڑے تو فوراً {EMERGENCY_NUMBER} پر کال کریں۔"
        question = "کیا اس وقت کوئی آپ کے ساتھ ہے؟"
        severe = None  # already handled above
    elif re.search(r"پیچھا|نگرانی|جاسوسی|زہر|نظر رکھ|peecha|picha|nigrani|jasoosi|zehar|zeher|watching|following", t):
        opening = "یہ واقعی بہت ڈراؤنا اور پریشان کن لگ رہا ہے۔ میں یہیں آپ کے ساتھ ہوں۔"
        support = "چاہے یہ کتنا ہی حقیقی محسوس ہو، آپ کو اس کا اکیلے سامنا نہیں کرنا۔"
        help_line = (f"براہ کرم ابھی کسی قابلِ اعتماد شخص کو بتائیں، اور اگر آپ خود کو غیر محفوظ محسوس کریں تو "
                     f"{EMERGENCY_NUMBER} پر کال کریں یا قریبی ہسپتال جائیں۔")
        question = "کیا آپ کے پاس کوئی ہے جو کچھ دیر آپ کے ساتھ رہ سکے؟"
    elif re.search(r"آواز|awaz|awaaz|voice", t):
        opening = "ایسی آوازیں سننا واقعی بہت بوجھل اور پریشان کن ہو سکتا ہے۔ مجھے خوشی ہے کہ آپ نے مجھے بتایا۔"
        support = "اس وقت کسی محفوظ شخص کے قریب یا کسی جانی پہچانی جگہ پر رہنے کی کوشش کریں۔"
        help_line = "اچھا ہوگا کہ آپ کسی قابلِ اعتماد شخص سے اور کسی ڈاکٹر یا کونسلر سے بھی اس بارے میں بات کریں۔"
        question = "کیا اس وقت آپ اکیلے ہیں، یا کوئی آپ کے قریب ہے؟"
    else:
        opening = "یہ تجربہ واقعی بہت پریشان کن اور الجھا دینے والا لگ رہا ہے۔ مجھے بتانے کا شکریہ۔"
        support = "اس لمحے آپ اکیلے نہیں ہیں۔ اپنے اردگرد کی چیزوں پر دھیان دیں اور ایک لمبی، آہستہ سانس لیں۔"
        help_line = "براہ کرم کسی قابلِ اعتماد شخص کو بتائیں اور جلد کسی ڈاکٹر یا کونسلر سے بات کریں۔"
        question = "اس وقت آپ کے اردگرد کیا ہو رہا ہے؟"

    if severe:
        help_line = (f"براہ کرم ابھی اپنے قریب کسی شخص کو بتائیں، اور اگر لگے کہ آپ کسی خیال پر عمل کر سکتے ہیں تو "
                     f"فوراً {EMERGENCY_NUMBER} یا اُمنگ {UMANG_NUMBER} پر کال کریں۔")

    middle = "جو کچھ آپ گزار رہے ہیں، اس کی وجہ سے اس وقت آپ کے ذہن پر بہت بوجھ ہو سکتا ہے۔"
    return _with_comfort(f"{opening}\n\n{middle}\n\n{support}\n\n{help_line}\n\n{question}", "psychosis")


def build_psychosis_support_response(user_text, name="", role="default"):
    """Calm, non-validating psychosis-safe response."""
    # Seeing people who have died / sensing someone in the room
    # (and NOT being called / urged to join them, which is handled as high risk below)
    kind = _perception_kind(user_text)
    if kind and not _DECEASED_CALLING_RE.search(user_text or ""):
        return build_perception_response(user_text, kind)
    if session.get("language") == "ur":
        return _build_psychosis_support_response_ur(user_text)
    text = normalize_for_safety(user_text)
    display_name = f"{name}, " if name else ""

    idp_contexts = {
        "student": "With studies, routine, and safety feeling unsettled, your mind may be under a lot of strain right now.",
        "homemaker": "After carrying family needs and daily uncertainty, your mind may be under a lot of strain right now.",
        "daily_worker": "With work, safety, and daily survival feeling uncertain, your mind may be under a lot of strain right now.",
        "unemployed": "With uncertainty around work and family responsibilities, your mind may be under a lot of strain right now.",
        "parent": "When you are trying to protect family while carrying fear inside, your mind may be under a lot of strain right now.",
        "default": "With everything you have been going through, your mind may be under a lot of strain right now."
    }
    idp_line = idp_contexts.get(role, idp_contexts["default"])

    severe_markers = [
        "hurt", "harm", "kill", "end", "die", "suicide", "go to her", "go to him",
        "join her", "join him", "join them", "i should go", "i should join"
    ]
    is_severe = any(m in text for m in severe_markers)

    if any(w in text for w in ["watching", "following", "spying", "tracking", "trying to kill", "harm me"]):
        opening = f"{display_name}that sounds really frightening and unsettling. I am here with you."
        support = "Even if it feels very real, you do not have to handle this alone."
        help_line = "Please reach out to someone you trust now, and if you feel unsafe, call 1122 or go to a nearby hospital."
        question = "Is there someone near you who can stay with you for a while?"
    elif any(w in text for w in ["voice", "voices", "hearing", "hear", "talking to me"]):
        opening = f"{display_name}hearing something like that must feel really overwhelming. I am glad you told me."
        support = "Try to stay near a safe person or a familiar place right now."
        help_line = "It would be a good idea to speak with someone you trust and also a doctor or counsellor who can support you through this."
        question = "Are you alone right now, or is someone close by?"
    elif any(w in text for w in ["dead", "died", "passed", "calling me", "go to her", "join her", "go to him", "join him"]):
        opening = f"{display_name}that sounds deeply confusing and painful, especially when it feels connected to someone you have lost."
        if any(w in text for w in ["go to her", "join her", "go to him", "join him", "i should go", "i should join", "want to die", "kill myself", "end my life"]):
            support = "Please do not act on that feeling; stay where you are and keep yourself close to someone safe."
            help_line = "Tell a trusted person near you right now, and if the urge feels strong, call 1122 or Umang at 0311-7786264 immediately."
            question = "Can you sit with someone you trust right now?"
        else:
            support = "This can feel frightening, but you do not have to treat the dream as something you must follow."
            help_line = "Since you feel scared, stay close to someone you trust and talk to them about what you saw."
            question = "Can you tell your sister or someone nearby what the dream made you feel?"
    else:
        opening = f"{display_name}that sounds really distressing and confusing to experience. I am glad you shared it with me."
        support = "You are not alone in this moment; try to keep your body grounded by noticing where you are and taking one slow breath."
        help_line = "Please consider telling someone you trust and speaking with a doctor or counsellor soon."
        question = "What is happening around you right now?"

    if is_severe and "1122" not in help_line:
        help_line = "Please tell someone near you right now, and if you feel at risk of acting on it, call 1122 or Umang at 0311-7786264 immediately."

    return _with_comfort(
        f"{opening}\n\n{idp_line}\n\n{support}\n\n{help_line}\n\n{question}", "psychosis"
    )




def get_crisis_support_message(name, memory=None):
    opener = f"{name}, " if name else ""

    # --- variations ---
    openings = [
        "I want to slow this moment down with you for a second.",
        "Let’s pause here together for a moment.",
        "Take a breath with me for a second.",
        "I’m here with you right now — let’s go gently."
    ]

    hope_lines = [
        "This shall pass too, even if it doesn’t feel like it right now.",
        "What you’re feeling is heavy, but it is not permanent.",
        "Even the longest nights eventually turn into morning.",
        "This moment is not your whole life."
    ]

    strength_lines = [
        "You’ve already survived difficult moments before.",
        "There is strength in you, even if it feels hidden right now.",
        "The fact that you are here shows your strength.",
        "You are doing better than you think."
    ]

    islamic_lines = [
        "Allah reminds us: 'With hardship comes ease.'",
        "Allah does not burden a soul beyond what it can bear.",
        "Even in hardship, there is a path toward ease.",
        "Allah sees your struggle, even the quiet ones."
    ]

    support_lines = [
        "You don’t have to carry everything alone.",
        "It’s okay to take things one small step at a time.",
        "Right now, just one calm breath is enough.",
        "You don’t need to fix everything today."
    ]

    closing_lines = [
        "We can talk more… tell me what’s on your mind.",
        "I’m here to listen… what feels heaviest right now?",
        "You can share anything with me… I’m here.",
        "Tell me what has been weighing on you the most."
    ]

    # --- optional personalization (IDP memory) ---
    displacement_line = ""
    if memory and memory.get("living_place"):
        place = memory.get("living_place")
        displacement_line = f"Being in {place} can make everything feel even heavier sometimes.\n\n"

    # --- build message ---
    message = (
        f"{opener}{random.choice(openings)}\n\n"
        f"{random.choice(hope_lines)}\n\n"
        f"{random.choice(strength_lines)}\n\n"
        f"{support_lines[0]}\n\n"
        f"{displacement_line}"
        f"{random.choice(islamic_lines)}\n\n"
        f"If you feel unsafe, please call Umang 0311-7786264 or 1122.\n\n"
        f"{random.choice(support_lines)}\n\n"
        f"{random.choice(closing_lines)}"
    )

    return message


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .context import (  # noqa: E402
    PSYCHOSIS_PATTERNS,
    PSYCHOSIS_SUPPORT_VERSES,
    QURAN_HOPE_VERSES,
)
from .core import (  # noqa: E402
    ACTION_WORDS,
    ALLOWED_PHONE_NUMBERS,
    BLOCKED_TOPICS,
    CRISIS_EXIT_SAFE_TURNS,
    CRISIS_PATTERNS,
    EMERGENCY_NUMBER,
    ESCALATION_PATTERNS,
    FAITH_WORDS,
    FINALITY_WORDS,
    FRAMING_PATTERNS,
    JAILBREAK_PATTERNS,
    MANIPULATION_PATTERNS,
    RELEASE_WORDS,
    SAFETY_CONFIRM_PHRASES,
    SYNONYM_PATTERNS,
    UMANG_NUMBER,
    client,
    emotion_model,
    tfidf,
)
from .language import (  # noqa: E402
    URDU_EXAMPLES,
    URDU_STYLE_RULES,
    _ensure_one_followup,
    _polish_english_reply,
    _polish_urdu_reply,
    _reply_language_ok,
    _umang_reminder,
)
from .text_utils import (  # noqa: E402
    clean_text,
    has_grief_intent,
    is_metaphor_kill,
    normalize_for_safety,
    normalize_text,
)

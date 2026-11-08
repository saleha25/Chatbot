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
    }

    # Keep the original trained/loaded patterns too, if present.
    for category, patterns in PSYCHOSIS_PATTERNS.items():
        score = 0
        for pattern, weight in patterns:
            if re.search(pattern, t):
                score += weight
        if score > 0:
            detected[category] = detected.get(category, 0) + score

    for category, patterns in expanded_patterns.items():
        score = 0
        for pattern, weight in patterns:
            if re.search(pattern, t):
                score += weight
        if score > 0:
            detected[category] = detected.get(category, 0) + score

    if not detected:
        return {"psychosis_detected": False, "level": "safe"}

    top_category = max(detected, key=detected.get)
    top_score = detected[top_category]

    if top_category == "command_hallucination" or top_score >= 11:
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
            return _ensure_one_followup(response, user_text) if '_ensure_one_followup' in globals() else response
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
    return random.choice(options)
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


def build_crisis_response(user_text, name=""):
    """Safety response when risk is detected. Fixed wording on purpose (safety-critical).

    - First time in this conversation: full safety message.
    - Risk comes up again: a different opening, so the user feels heard, not looped.
    - A method (pills, poison...) is mentioned: refuse details and ask about access.
    """
    repeat = session.get("crisis_step") in ("initial", "followup", "repeat")
    text_l = (user_text or "").lower()
    mentions_method = any(w in text_l for w in CRISIS_METHOD_WORDS)

    session["crisis_step"] = "repeat" if repeat else "initial"
    session["crisis_safe_turns"] = 0
    language = session.get("language", "en")

    if language == "ur":
        if repeat:
            opening = (
                "دوبارہ بتانے کا شکریہ۔ میں آپ کی بات کو بہت سنجیدگی سے لے رہی ہوں، "
                "اور میں اب بھی یہیں آپ کے ساتھ ہوں۔"
            )
        else:
            opening = (
                "آپ نے مجھے بتایا، اس کے لیے شکریہ۔ آپ جو کچھ اٹھائے ہوئے ہیں وہ بہت بھاری ہے، "
                "اور آپ کی زندگی بہت قیمتی ہے۔"
            )

        if mentions_method:
            middle = (
                "میں دوائیوں کے نام نہیں بتا سکتی، کیونکہ مجھے آپ کی حفاظت کی فکر ہے۔ "
                "ایک سیدھا سوال ہے: کیا آپ اپنی زندگی ختم کرنے کا سوچ رہے ہیں؟ "
                "کیا اس وقت آپ کے پاس گولیاں یا کوئی ایسی چیز ہے جس سے آپ خود کو نقصان پہنچا سکیں؟ "
                "اگر ہے تو براہ کرم اسے خود سے دور کر دیں یا کسی قابلِ اعتماد شخص کو دے دیں۔"
            )
        else:
            middle = (
                "ایک سیدھا سوال ہے: کیا آپ اپنی زندگی ختم کرنے کا سوچ رہے ہیں؟ "
                "اور کیا اس وقت کوئی آپ کے پاس ہے؟"
            )

        msg = (
            f"{opening}\n\n{middle}\n\n"
            f"براہ کرم ابھی اُمنگ سے {UMANG_NUMBER} پر رابطہ کریں (مفت، 24 گھنٹے)، "
            f"یا فوری خطرے کی صورت میں {EMERGENCY_NUMBER} پر کال کریں۔"
        )
        if user_invited_faith():
            msg += "\n\nاللہ آپ کے دل کا درد دیکھتا ہے، اور اس کی رحمت کا دروازہ کبھی بند نہیں ہوتا۔"
        return msg

    if repeat:
        opening = (
            "Thank you for telling me again. I'm taking this seriously, "
            "and I'm still right here with you."
        )
    else:
        opening = (
            "I'm really glad you told me. What you're carrying sounds unbearably heavy, "
            "and your life matters."
        )

    if mentions_method:
        middle = (
            "I can't share medicine names, because I'm worried about your safety. "
            "Can I ask you directly: are you thinking about ending your life? "
            "Do you have pills or anything you could hurt yourself with near you right now? "
            "If you do, please move them away or give them to someone you trust."
        )
    else:
        middle = (
            "Can I ask you directly: are you thinking about ending your life? "
            "And is anyone with you right now?"
        )

    msg = (
        f"{opening}\n\n{middle}\n\n"
        f"Please call Umang at {UMANG_NUMBER} (free, 24/7), "
        f"or {EMERGENCY_NUMBER} if you're in danger right now."
    )
    if user_invited_faith():
        msg += "\n\nAllah sees the pain in your heart, and His mercy is never closed to you."
    return msg


def _crisis_risk_signal(text):
    """Does THIS message still show active risk?"""
    norm = normalize_for_safety(text or "")
    if direct_crisis_override(norm):
        return True
    try:
        return detect_crisis(norm) in ("crisis", "emergency")
    except Exception:
        return False


def _generate_crisis_followup_llm(user_message, exiting):
    if not os.environ.get("OPENAI_API_KEY"):
        return None

    language = session.get("language", "en")
    faith_ok = user_invited_faith()

    if exiting:
        stage_rule = (
            "The user now seems steadier. Warmly acknowledge this positive shift. "
            "If relevant, give ONE small tip for tonight. Gently remind them they can "
            f"come back anytime and that Umang ({UMANG_NUMBER}) is available 24/7."
        )
    else:
        stage_rule = (
            "Safety mode is still active. At the end, mention once, gently, that Umang "
            f"({UMANG_NUMBER}, free, 24/7) is there if the heavy thoughts come back."
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
- If they describe a specific struggle, give ONE small concrete coping step that fits it:
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


def _crisis_followup_fallback(exiting):
    """Used only if the API fails."""
    language = session.get("language", "en")

    if language == "ur":
        if exiting:
            return (
                "یہ سن کر اچھا لگا کہ اب آپ کچھ بہتر محسوس کر رہے ہیں۔ آج رات اپنے ساتھ نرمی رکھیں، "
                "تھوڑا پانی پئیں اور چند لمبی سانسیں لیں۔ آپ جب چاہیں دوبارہ بات کر سکتے ہیں، "
                f"اور اگر بھاری خیالات واپس آئیں تو اُمنگ {UMANG_NUMBER} پر 24 گھنٹے دستیاب ہے۔"
            )
        return (
            "بات جاری رکھنے کا شکریہ۔ جو کچھ آپ نے جھیلا ہے، اس کے بعد ایسا محسوس ہونا سمجھ میں آتا ہے۔ "
            "آئیں ایک چھوٹی سی چیز کریں: چار تک گن کر سانس اندر لیں، چار تک روکیں، اور چھ تک گن کر آہستہ باہر نکالیں۔ "
            f"اگر بھاری خیالات واپس آئیں تو براہ کرم اُمنگ {UMANG_NUMBER} یا {EMERGENCY_NUMBER} پر رابطہ کریں۔"
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
        "and breathe out slowly for 6, three times. If the heavy thoughts come back, please call "
        f"Umang at {UMANG_NUMBER} (free, 24/7) or {EMERGENCY_NUMBER} in an emergency."
    )


def get_crisis_followup_response(name, user_message):
    """Called for every message while crisis_active is True."""
    text = normalize_for_safety(user_message or "")

    # 1. New risk signal -> full safety message again, reset the calm counter
    if _crisis_risk_signal(user_message):
        session["crisis_active"] = True
        return build_crisis_response(user_message, name)

    # 2. Count calm turns. A clear safety confirmation exits immediately.
    if any(p in text for p in SAFETY_CONFIRM_PHRASES):
        session["crisis_safe_turns"] = CRISIS_EXIT_SAFE_TURNS
    else:
        session["crisis_safe_turns"] = session.get("crisis_safe_turns", 0) + 1

    exiting = session["crisis_safe_turns"] >= CRISIS_EXIT_SAFE_TURNS

    # 3. Real, context-aware reply from the LLM (fallback if API fails)
    reply = _generate_crisis_followup_llm(user_message, exiting) or _crisis_followup_fallback(exiting)

    # Safety line is enforced by code, never left to the LLM
    if UMANG_NUMBER not in reply:
        reply = reply.rstrip() + "\n\n" + _umang_reminder(session.get("language", "en"))

    # 4. Update state
    if exiting:
        session["crisis_active"] = False
        session["crisis_level"] = "safe"
        session["crisis_step"] = ""
        session["crisis_safe_turns"] = 0
    else:
        session["crisis_active"] = True
        session["crisis_step"] = "followup"

    return reply

def build_psychosis_support_response(user_text, name="", role="default"):
    """Calm, non-validating psychosis-safe response."""
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

    return f"{opening}\n\n{idp_line}\n\n{support}\n\n{help_line}\n\n{question}"




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

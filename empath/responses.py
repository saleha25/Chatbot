"""Normal chat replies: LLM prompt, adaptive memory, fallbacks.

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



def get_recent_bot_responses(limit=4):
    history = list(session.get("history", []))
    bot_messages = [item.get("content", "") for item in history if item.get("role") == "assistant"]
    return bot_messages[-limit:]


def pick_fresh_response(options, fallback=None):
    recent = " ".join(get_recent_bot_responses()).lower()
    cleaned_options = []
    for option in options:
        probe = option.lower()[:80]
        if probe and probe not in recent:
            cleaned_options.append(option)

    if cleaned_options:
        return random.choice(cleaned_options)
    if fallback:
        return fallback
    return random.choice(options)


def build_wellbeing_context(user_id):
    if not user_id:
        return ""

    latest_log = get_latest_wellbeing_log(user_id)
    if not latest_log:
        return ""

    parts = []

    sleep = latest_log["sleep_hours"]
    mood = latest_log["mood"]
    energy = latest_log["energy"]

    if sleep is not None:
        if sleep <= 4:
            parts.append(f"You have only been sleeping around {sleep:g} hours, so no wonder your body feels worn out.")
        elif sleep <= 6:
            parts.append(f"Your sleep has been around {sleep:g} hours, which is probably leaving you drained.")
        else:
            parts.append(f"Your sleep is around {sleep:g} hours lately.")

    mood_map = {
        "very_low": "Your mood has been feeling very low.",
        "low": "Your mood has been feeling heavy.",
        "anxious": "There has been a lot of worry sitting in your system.",
        "okay": "Your mood seems a little steadier right now.",
        "good": "There are still some signs of steadiness in you."
    }
    if mood in mood_map:
        parts.append(mood_map[mood])

    energy_map = {
        "very_low": "Your energy sounds completely drained.",
        "low": "Your energy has been low.",
        "medium": "Your energy is there in small amounts.",
        "high": "Your energy seems stronger than before."
    }
    if energy in energy_map:
        parts.append(energy_map[energy])

    return " ".join(parts[:2])


def maybe_add_soft_faith_line(user_message, crisis_level="safe"):
    text = normalize_for_safety(user_message)

    if crisis_level in ["crisis", "emergency"]:
        return ""

    if any(x in text for x in ["sleep", "tired", "heavy", "hard", "burden", "worried", "hopeless"]):
        return random.choice([
            "Even in hard seasons, ease does not disappear forever.",
            "Sometimes faith helps by reminding us that hard phases do move.",
            "A person can feel worn down and still not be at the end of their story."
        ])

    return ""


def build_local_fallback_response(user_message, sess, emotion="unknown"):
    name = sess.get("name", "")
    role = sess.get("role", "default")
    history = list(sess.get("history", []))

    hardships = detect_idp_hardships(history)
    session["idp_hardships"] = hardships

    context_line = get_idp_context_line(role, hardships)
    idp_line = context_line
    faith_line = maybe_add_soft_faith_line(user_message, sess.get("crisis_level", "safe"))
    opener = f"{name}, " if name else ""
    lowered = user_message.lower()

    sleep_words = ["sleep", "slept", "insomnia", "night", "awake"]
    tired_words = ["tired", "drained", "exhausted", "fatigue", "weak", "energy"]

    # Strict rule: only discuss sleep/rest when user brings sleep or tiredness first.
    if any(w in lowered for w in sleep_words + tired_words):
        options = [
            f"{opener}that sounds exhausting. {idp_line} What usually makes nights hardest for you?",
            f"{opener}when rest goes off like this, the whole day can feel heavier. {idp_line} How long has this been going on?",
            f"{opener}that kind of tiredness can build up quickly. Is it more broken sleep, worry, noise around you, or not getting the chance to rest at all?",
            f"{opener}your body sounds worn out. What happens first — racing thoughts, interruptions around you, or no real chance to rest?"
        ]
        return pick_fresh_response(options)

    # Mood / sadness: stay on mood, but keep a light Pakistan-IDP frame.
    if emotion in ["depression", "sadness", "grief"]:
        options = [
            f"{opener}that sounds really heavy. {idp_line} What has been weighing on you the most today?",
            f"{opener}I can tell this is not a small moment for you. Living with uncertainty after displacement can make feelings harder to name. What feels hardest right now?",
            f"{opener}you do not sound okay. {context_line} Is it more your thoughts, your heart, or everything together today?",
            f"{opener}{idp_line} When life feels unsettled, even small things can feel too much. What has been draining you the most lately?"
        ]
        return pick_fresh_response(options)

    if emotion in ["anxiety", "fear", "stress", "ptsd"]:
        options = [
            f"{opener}your mind sounds really overloaded right now. {idp_line} What keeps circling in your head the most?",
            f"{opener}that kind of pressure can make it hard to settle. If things around you are uncertain, what has been making it hardest to feel calm?",
            f"{opener}it feels like your system has been on edge for too long. {context_line} What part of the day feels most intense for you?",
            f"{opener}{idp_line} What has been putting the most pressure on you lately?"
        ]
        return pick_fresh_response(options)

    options = [
        f"{opener}I am with you. {idp_line} What feels most difficult today?",
        f"{opener}you can say it plainly here. When life is unsettled after displacement, feelings can become hard to explain. What has been sitting on your mind the most?",
        f"{opener}this seems bigger than one small bad moment. {faith_line} {idp_line} What has been wearing you down lately?",
        f"{opener}{context_line} What feels hardest to carry right now?"
    ]
    return pick_fresh_response(options)

def get_response(user_message, sess, crisis_level="safe", emotion="unknown"):
    name = sess.get("name", "")
    role = sess.get("role", "default")
    severity = sess.get("severity", "unknown")
    history = list(sess.get("history", []))
    user_id = sess.get("user_id")
    memory_profile = sess.get("user_memory_profile", "")

    cause = detect_displacement_cause(history)
    phrase = get_displacement_phrase(cause)
    session["displacement_cause"] = cause

    hardships = detect_idp_hardships(history)
    session["idp_hardships"] = hardships
    idp_context_line = get_idp_context_line(role, hardships)

    retrieved = retrieve_from_training_data(user_message, emotion, role, top_k=2)

    retrieval_context = ""
    if retrieved:
        retrieval_context = "\n\nMOST RELEVANT RESPONSES FROM OUR IDP TRAINING DATA:\n"
        for i, r in enumerate(retrieved, 1):
            retrieval_context += (
                f"\nExample {i} [source={r['source']}, similarity={r['similarity']}]:\n"
                f"Situation: {r['question'][:150]}\n"
                f"Counselor response: {r['answer'][:400]}\n"
            )
        retrieval_context += "\nIMPORTANT: Use these as style and support references. Rephrase naturally. Do not copy word for word."

    insight = IDP_SURVEY_INSIGHTS.get(role) or {}
    survey_note = ""
    if insight:
        survey_note = (
            f"\nOUR REAL IDP SURVEY DATA for '{role}' (n={insight['count']} actual Pakistani IDPs):\n"
            f"Most common depression issues: {', '.join(insight['top_phq'])}\n"
            f"Most common anxiety issues: {', '.join(insight['top_gad'])}\n"
        )

    wellbeing_note = ""
    if user_id:
        latest_log = get_latest_wellbeing_log(user_id)
        if latest_log:
            parts = []
            if latest_log["sleep_hours"] is not None:
                parts.append(f"last recorded sleep: {latest_log['sleep_hours']} hours")
            if latest_log["mood"]:
                parts.append(f"last recorded mood: {latest_log['mood']}")
            if latest_log["energy"]:
                parts.append(f"last recorded energy: {latest_log['energy']}")
            if parts:
                wellbeing_note = "\nPREVIOUS WELLBEING INFO FOR THIS RETURNING USER: " + ", ".join(parts)

    hardship_note = ""
    if hardships:
        hardship_note = f"\nDETECTED IDP HARDSHIPS FROM CONVERSATION: {', '.join(hardships)}\nSuggested emotional framing: {idp_context_line}\n"

    advice_note = ""
    last_advice = sess.get("last_advice", "")
    last_scores = sess.get("last_assessment_scores", {})
    if last_advice:
        advice_note = (
            f"\nRECENT ASSESSMENT RESULTS FOR THIS USER:\n"
            f"PHQ-9 (Depression): {score_to_10(last_scores.get('phq9', 0), 27)}/10 | "
            f"GAD-7 (Anxiety): {score_to_10(last_scores.get('gad7', 0), 21)}/10 | "
            f"WHO-5 (Wellbeing): {score_to_10(last_scores.get('who5', 0), 25)}/10\n"
            f"Advice already given:\n{last_advice}\n"
            f"INSTRUCTION: If the user mentions sleep, routines, anxiety coping, helplines, or professional support, "
            f"gently follow up on how they are doing with the guidance above. Do not repeat the full advice — "
            f"just check in naturally and adjust based on what they share.\n"
        )

    system_prompt = f"""
You are Empath AI — a supportive mental health chatbot designed specifically for internally displaced people (IDPs) in Pakistan.

PERSON:
Name={name or 'unknown'}
Role={role}
Emotion={emotion}
Crisis={crisis_level}
Severity={severity}

LEARNED USER MEMORY PROFILE (compact profile learned from previous chats, not full chat):
{memory_profile}

MEMORY RULES:
- Use this profile gently when relevant.
- Do not quote or repeat old conversations.
- Do not say "last time you said" unless the user asks.
- Adapt support using learned role, displacement, living place, stressors, and wellbeing patterns.

DISPLACEMENT CONTEXT:
Displacement cause detected silently: {cause}
Natural phrase if needed: "{phrase}"
IMPORTANT: This chatbot is built for Pakistani IDPs. Use a light IDP-aware frame when the user is distressed, especially around camp, shelter, NGO, relatives, temporary living, lost routine, privacy, studies, family pressure, or uncertainty.
Do not overuse displacement wording in every sentence. If the user has shared a specific living place, use that exact wording and do not replace it with another place.
If the cause is unknown, do not assume flood, conflict, or any specific reason.
{hardship_note}
{survey_note}
{wellbeing_note}
{advice_note}
{retrieval_context}

YOUR STYLE:
1. Speak like a real person, not like a counselor reading a script.
2. Use natural English with short, direct sentences.
3. Sound warm and grounded, not formal.
4. For general sadness or confusion, include a gentle Pakistan-IDP context when natural, such as disrupted routine, temporary place, camp, shelter, NGO, relatives, privacy, studies, family pressure, or uncertainty.
5. Tailor the response to the person's role and recent wellbeing data if available.
6. Keep the reply to 3 or 4 short paragraphs or 4 to 6 sentences total.
7. Ask no more than one gentle question.
8. Avoid filler phrases like "I'm sorry to hear that", "I understand", "that sounds difficult", or "would you like to share more".
9. Avoid repetitive crisis warnings or repeating the same structure as the last reply.
10. Do not sound clinical, preachy, or overly polished.
11. Support first, assessment later.
12. Never provide self-harm information.
13. If crisis is present, mention Umang: 0311-7786264.
14. Strict rule: do not introduce sleep, sleeping hours, night rest, or energy unless the user explicitly mentions sleep, tiredness, rest, fatigue, or energy first.
15. If this is a returning user, you may gently notice patterns over time, but do not sound technical.
16. If faith fits naturally, add only one soft line. Keep it brief and natural.
17. SAFETY NET: the user may write in English, Urdu script or Roman Urdu. If the current message shows ANY sign of suicidal thoughts, a wish to die, or self-harm, in any language or spelling, reply with exactly {SAFETY_CHECK_TOKEN} and nothing else.
18. Never describe a wish to die as "natural" or "normal".

BAD STYLE:
- robotic empathy
- formal therapy wording
- repeated sentence patterns
- too many questions
- long lectures
"""

    try:
        messages = []
        for item in history[-6:]:
            if isinstance(item, dict) and "role" in item and "content" in item:
                messages.append(item)

        messages.append({"role": "user", "content": user_message})

        response = client.responses.create(
            model="gpt-4.1-mini",
            instructions=system_prompt,
            input=messages,
            max_output_tokens=220,
        )

        return response.output_text.strip()

    except Exception as e:
        print(f"OpenAI error: {e}")
        if retrieved:
            return retrieved[0]["answer"][:400]
        return build_local_fallback_response(user_message, sess, emotion)
# ================== PRO RESPONSE ENGINE ==================

def simple_memory_update(session, msg):
    msg = msg.lower()

    memory = session.get("simple_memory", {})

    if "money" in msg:
        memory["stress"] = "financial"
    elif any(w in msg for w in ["study","exam","focus"]):
        memory["stress"] = "academic"
    elif detect_family_stress(msg):
        memory["stress"] = "family"

    if "tired" in msg:
        memory["energy"] = "low"

    if any(w in msg for w in ["sad","low","down"]):
        memory["mood"] = "low"

    session["simple_memory"] = memory
    return memory




def smart_response(user_message, session):
    msg = user_message.lower()

    # Neutral/supportive family presence should not be treated as family stress
    if is_neutral_family_presence(msg):
        return "That is good to hear. Having your family with you can be a source of support.\n\nHow are you feeling personally right now?"

    if "happy" in msg:
        return random.choice([
            "That’s really nice to hear. What made today better?",
            "I’m glad things feel lighter today. What helped?"
        ])

    if "money" in msg:
        return random.choice([
            "Money stress can feel really heavy. What part is worrying you most?",
            "Financial pressure can stay on your mind. What feels hardest right now?"
        ])

    if "study" in msg:
        return random.choice([
            "Trying to study like this is really hard. What part feels most difficult?",
            "It’s not easy to focus when your mind is carrying so much. What’s hardest right now?"
        ])

    if "tired" in msg:
        return random.choice([
            "That sounds exhausting. Is it more physical or mental tiredness?",
            "Feeling that tired can drain everything. What’s taking most of your energy?"
        ])

    if any(w in msg for w in ["sad","low","down"]):
        place = session.get("living_place", "")
        if place:
            return f"Some days feel heavier, especially living in {place}. What kind of low is it today?"
        return "Some days just feel low. What kind of low is it today?"

    return None




# ─────────────────────────────────────────────
# NATURAL RESPONSE ENGINE
# ─────────────────────────────────────────────
def _recent_dialogue_for_prompt(limit=8):
    history = list(session.get("history", []))[-limit:]
    lines = []
    for item in history:
        role = item.get("role", "user")
        content = str(item.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _compact_user_context(sess, emotion="unknown", wellbeing_info=None):
    name = sess.get("name", "") or "the user"
    role = sess.get("role", "default") or "default"
    memory = sess.get("user_memory_profile", "") or ""
    living_place = sess.get("living_place", "") or ""

    context = [
        f"Name: {name}",
        f"Role/profession: {role}",
        "Project context: Empath AI supports internally displaced people in Pakistan; keep this as gentle background context.",
        "Do not ask whether the user is an IDP and do not repeatedly say 'as an IDP' or 'because you are displaced'.",
    ]
    if living_place:
        context.append(f"Current place/living context: {living_place}")
    if memory:
        context.append(f"Saved memory summary: {memory}")
    if emotion and emotion != "unknown":
        context.append(f"Detected emotional signal: {emotion}")
    if wellbeing_info:
        context.append(f"Wellbeing info extracted from latest message: {wellbeing_info}")
    return "\n".join(context)


# ─────────────────────────────────────────────
# ADAPTIVE MEMORY AND RESPONSE LEARNING — NO EMBEDDINGS / DATASET-GUIDED
# ─────────────────────────────────────────────
def _safe_lower(value):
    return str(value or "").lower().strip()


def _tone_tags(text):
    """Light semantic tags for memory summaries. These guide the LLM; they are not rigid replies."""
    t = normalize_for_safety(text or "")
    tags = set()
    groups = {
        "faith": ["allah", "dua", "prayer", "praying", "namaz", "quran", "sabr", "tawakkul", "shukar", "thank allah"],
        "family": ["family", "children", "kids", "parents", "mother", "father", "wife", "husband", "responsibility"],
        "work_money": ["job", "work", "money", "income", "unemployed", "earn", "fees", "expense", "provide"],
        "sleep_energy": ["sleep", "slept", "tired", "energy", "exhausted", "drained", "weak"],
        "sadness": ["sad", "low", "worse", "worst", "hopeless", "empty", "cry", "depressed"],
        "anxiety": ["stress", "stressed", "worried", "anxious", "panic", "fear", "overthinking"],
        "hope_positive": ["better", "happy", "hope", "hopeful", "good news", "peace", "peaceful", "thankful", "grateful", "handled", "managed"],
        "idp_life": ["camp", "shelter", "tent", "flood", "relief", "temporary", "lost home", "left home", "displaced"],
    }
    for tag, words in groups.items():
        if any(w in t for w in words):
            tags.add(tag)
    return tags


def _classify_assistant_style(text):
    """Estimate previous assistant style so future replies can adapt and avoid repetition."""
    t = _safe_lower(text)
    tags = []
    if any(w in t for w in ["allah", "quran", "dua", "sabr", "prayer", "may allah"]):
        tags.append("faith-based")
    if any(w in t for w in ["try", "small step", "routine", "one thing", "practical", "plan", "support"]):
        tags.append("practical")
    if any(w in t for w in ["i hear", "sounds", "makes sense", "not easy", "heavy", "with you"]):
        tags.append("validation")
    if any(w in t for w in ["reflect", "notice", "inside", "meaning", "heart", "calm", "gentle"]):
        tags.append("reflective")
    words = t.split()
    if len(words) <= 45:
        tags.append("short")
    elif len(words) >= 95:
        tags.append("deep")
    return tags or ["supportive"]


def _user_engaged_after(text):
    """Approximate engagement from the user's reply following an assistant message."""
    t = _safe_lower(text)
    if not t:
        return "none"
    if len(t.split()) >= 7:
        return "engaged"
    if any(w in t for w in ["yes", "thanks", "thank", "ok", "okay", "good", "right", "true"]):
        return "light"
    return "brief"


def build_adaptive_memory_context(user_id, current_message=""):
    """Summarise long-term memory from SQLite without embeddings.

    Uses chat_history, wellbeing_logs, assessments, and previous assistant responses.
    The summary is sent to the LLM as guidance only; it should never be dumped to the user.
    """
    if not user_id:
        return "No saved long-term memory yet."
    try:
        db = get_db()
        rows = db.execute("""
            SELECT sender, message, timestamp
            FROM chat_history
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 40
        """, (user_id,)).fetchall()
        rows = list(reversed(rows))
        user_msgs = [str(r["message"]) for r in rows if r["sender"] == "user"]
        assistant_msgs = [str(r["message"]) for r in rows if r["sender"] == "assistant"]
        all_user_text = " ".join(user_msgs[-20:])
        current_tags = _tone_tags(current_message)
        past_tags = _tone_tags(all_user_text)

        recurring = []
        if "family" in past_tags:
            recurring.append("family/responsibility")
        if "work_money" in past_tags:
            recurring.append("work, income, or financial pressure")
        if "sleep_energy" in past_tags:
            recurring.append("sleep or low energy")
        if "sadness" in past_tags:
            recurring.append("low mood/heaviness")
        if "anxiety" in past_tags:
            recurring.append("stress or worry")
        if "faith" in past_tags:
            recurring.append("faith as comfort")
        if "idp_life" in past_tags:
            recurring.append("living/adjustment pressures")

        current_norm = normalize_for_safety(current_message or "")
        trend_hint = ""
        if any(w in current_norm for w in ["better", "calm", "peace", "peaceful", "handled", "managed", "happy", "hopeful"]):
            if any(tag in past_tags for tag in ["sadness", "anxiety", "sleep_energy", "work_money", "family"]):
                trend_hint = "The user may be showing improvement or a lighter moment compared with earlier difficulty. Acknowledge growth gently."
        elif any(w in current_norm for w in ["again", "still", "worse", "worst", "hard again", "not better"]):
            trend_hint = "The user may be describing a repeated or worsening pattern. Recognize it gently without sounding like a database lookup."

        wellbeing_rows = db.execute("""
            SELECT sleep_hours, mood, energy, source_text, log_date
            FROM wellbeing_logs
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 5
        """, (user_id,)).fetchall()
        wellbeing_bits = []
        if wellbeing_rows:
            latest = wellbeing_rows[0]
            if latest["sleep_hours"] is not None:
                wellbeing_bits.append(f"latest sleep around {latest['sleep_hours']} hours")
            if latest["mood"]:
                wellbeing_bits.append(f"latest mood: {latest['mood']}")
            if latest["energy"]:
                wellbeing_bits.append(f"latest energy: {latest['energy']}")
            sleep_values = [float(r["sleep_hours"]) for r in wellbeing_rows if r["sleep_hours"] is not None]
            if len(sleep_values) >= 2:
                if sleep_values[0] > sleep_values[-1]:
                    wellbeing_bits.append("sleep appears somewhat improved compared with earlier logs")
                elif sleep_values[0] < sleep_values[-1]:
                    wellbeing_bits.append("sleep may have become lower than earlier logs")

        assess = db.execute("""
            SELECT phq9_total, gad7_total, who5_total, severity, advice_given, assessment_date
            FROM assessments
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
        """, (user_id,)).fetchone()
        assessment_bit = ""
        if assess and assess["phq9_total"] is not None:
            assessment_bit = f"Last assessment: PHQ9={assess['phq9_total']}, GAD7={assess['gad7_total']}, WHO5={assess['who5_total']}, severity={assess['severity']}."

        style_scores = {}
        openings = []
        for i, r in enumerate(rows):
            if r["sender"] == "assistant":
                msg = str(r["message"])
                first_sentence = re.split(r"[.!?]", msg.strip())[0].strip()
                if first_sentence:
                    openings.append(first_sentence[:120])
                next_user = ""
                for nxt in rows[i+1:]:
                    if nxt["sender"] == "user":
                        next_user = str(nxt["message"])
                        break
                engagement = _user_engaged_after(next_user)
                weight = 2 if engagement == "engaged" else 1 if engagement == "light" else 0
                for style in _classify_assistant_style(msg):
                    style_scores[style] = style_scores.get(style, 0) + weight

        preferred_styles = [k for k, v in sorted(style_scores.items(), key=lambda x: x[1], reverse=True) if v > 0][:3]
        if not preferred_styles:
            preferred_styles = ["warm validation", "gentle practical support"]
        avoid_openings = []
        seen = set()
        for op in reversed(openings[-8:]):
            key = op.lower()
            if key and key not in seen:
                avoid_openings.append(op)
                seen.add(key)
            if len(avoid_openings) >= 3:
                break

        profile = get_user_memory_profile(user_id) or ""
        lines = []
        if profile:
            lines.append("Saved profile: " + profile)
        if recurring:
            lines.append("Recurring themes: " + ", ".join(recurring) + ".")
        if current_tags:
            lines.append("Current message signals: " + ", ".join(sorted(current_tags)) + ".")
        if trend_hint:
            lines.append("Trend hint: " + trend_hint)
        if wellbeing_bits:
            lines.append("Wellbeing trend: " + "; ".join(wellbeing_bits) + ".")
        if assessment_bit:
            lines.append(assessment_bit)
        lines.append("Preferred response style inferred from user engagement: " + ", ".join(preferred_styles) + ".")
        if avoid_openings:
            lines.append("Avoid repeating these recent openings: " + " | ".join(avoid_openings) + ".")
        if user_msgs[-6:]:
            lines.append("Recent user statements: " + " | ".join(user_msgs[-6:]) + ".")
        return "\n".join(lines)
    except Exception as e:
        print("Adaptive memory context error:", e)
        return "Memory exists, but it could not be summarized safely."


def _fallback_natural_response(user_message, sess, emotion="unknown"):
    """Non-LLM fallback: broad, varied, context-aware, and not tied to one exact keyword reply."""
    name = sess.get("name", "")
    opener = f"{name}, " if name else ""
    t = normalize_for_safety(user_message or "")

    if is_metaphor_kill(user_message):
        base = random.choice([
            f"{opener}it sounds like this exam or project is taking a lot of energy from you, and you want to tackle it strongly. Even when life feels unsettled, breaking one hard thing into smaller steps can make it feel more possible.",
            f"{opener}I hear the pressure behind your words — you want to handle this properly, not let it defeat you. After everything you have been managing, even one focused step can be meaningful.",
            f"{opener}that sounds like determination mixed with stress. When responsibilities pile up, it can help to choose the next small task instead of carrying the whole project at once."
        ])
    elif any(w in t for w in ["peace", "peaceful", "prayer", "praying", "namaz", "allah", "dua", "tawakkul"]):
        base = random.choice([
            f"{opener}there is a calm strength in what you shared. Faith can sometimes give the heart a place to rest when life feels uncertain.",
            f"{opener}that sounds like a quiet kind of peace. When prayer settles the heart, even a difficult day can feel a little easier to face.",
            f"{opener}I can hear how much comfort this brings you. Holding onto trust in Allah can become a real source of steadiness."
        ])
    elif any(w in t for w in ["happy", "good news", "thankful", "grateful", "better", "hope", "will get better", "positive"]):
        base = random.choice([
            f"{opener}I can hear a brighter feeling in your words. After everything you have been managing, even one hopeful moment matters.",
            f"{opener}that sounds like something your heart really needed today. It is good to notice these lighter moments when they come.",
            f"{opener}there is hope in the way you are speaking. That does not erase the hard parts, but it gives you something steady to hold."
        ])
    elif any(w in t for w in ["job", "work", "money", "income", "unemployed", "earn", "support my family"]):
        base = random.choice([
            f"{opener}wanting to provide for your family while work is uncertain can feel deeply painful. Your care for them is clear, even before money or a job is solved.",
            f"{opener}that pressure makes sense. When income is uncertain, it can feel like your worth is being measured by what you can provide, but you are more than that.",
            f"{opener}I hear the responsibility behind your words. Supporting family can also mean staying steady, helping with small daily things, and taking one practical step at a time."
        ])
    elif any(w in t for w in ["family", "children", "parents", "responsibility", "pressure"]):
        base = random.choice([
            f"{opener}family responsibilities can sit very heavily on the heart, especially when life around you is already unsettled.",
            f"{opener}I can hear how much your family matters to you. Carrying that love and pressure together can be exhausting.",
            f"{opener}that sounds like a lot to hold quietly. Wanting to protect your family while managing your own feelings is not easy."
        ])
    elif any(w in t for w in ["sad", "low", "worse", "worst", "hopeless", "empty", "cry"]):
        base = random.choice([
            f"{opener}that sounds really heavy. When life has been unstable for a while, low feelings can come with extra weight.",
            f"{opener}I am listening. This does not sound like a small mood change; it sounds like something has been pressing on you.",
            f"{opener}some days feel hard to explain, but the heaviness is still real. You do not have to make it sound perfect here."
        ])
    elif any(w in t for w in ["stress", "stressed", "anxious", "worried", "fear", "panic", "overthinking"]):
        base = random.choice([
            f"{opener}your mind sounds crowded right now. When there is uncertainty around you, stress can become louder than usual.",
            f"{opener}that kind of pressure can make it hard to breathe freely. Let us slow it down together for a moment.",
            f"{opener}I hear the tension in this. Stress often feels bigger when there are too many things to manage at once."
        ])
    elif any(w in t for w in ["sleep", "tired", "exhausted", "drained", "energy", "weak"]):
        base = random.choice([
            f"{opener}that sounds draining. When rest is disturbed, emotions and daily responsibilities both become harder to carry.",
            f"{opener}low energy can quietly affect everything — mood, patience, focus, and hope.",
            f"{opener}your body and mind may be asking for gentleness, not more pressure."
        ])
    else:
        base = random.choice([
            f"{opener}I am with you. I want to understand what this moment feels like for you, not just reply quickly.",
            f"{opener}you can say it simply here. I will try to stay with the meaning behind your words.",
            f"{opener}I hear you. Let us take this one piece at a time, without making it feel like a form or test."
        ])
    return _ensure_one_followup(base, user_message)



def build_dataset_guidance_context(user_message, role="default", emotion="unknown"):
    """Use local training/testing assets as understanding signals, not as fixed replies.

    This keeps the chatbot from becoming rule-based: datasets and ML models guide
    tone, likely concerns, and risk awareness, while the final wording is generated
    naturally by the LLM/fallback layer.
    """
    parts = []
    try:
        role_key = role or "default"
        if 'IDP_SURVEY_INSIGHTS' in globals() and role_key in IDP_SURVEY_INSIGHTS:
            insight = IDP_SURVEY_INSIGHTS.get(role_key, {})
            top_phq = insight.get("top_phq", [])
            top_gad = insight.get("top_gad", [])
            if top_phq or top_gad:
                parts.append(
                    "Role-based survey signal: for this role, commonly observed concerns include "
                    + ", ".join((top_phq + top_gad)[:4])
                    + ". Use this only as background guidance."
                )
    except Exception as e:
        print("Dataset survey guidance error:", e)

    try:
        # Use retrieval system only as a lightweight topic signal, never as a copy-paste answer.
        retrieved = retrieve_from_training_data(user_message, emotion=emotion, role=role, top_k=2)
        if retrieved:
            topics = []
            for item in retrieved:
                topic = item.get("topic", "general")
                source = item.get("source", "training")
                sim = item.get("similarity", "")
                topics.append(f"{topic} ({source}, similarity {sim})")
            parts.append(
                "Training-data semantic signal: similar examples relate to "
                + "; ".join(topics)
                + ". Do not copy dataset answers; use only to understand likely context."
            )
    except Exception as e:
        print("Dataset semantic guidance error:", e)

    if emotion and emotion != "unknown":
        parts.append(f"ML emotion signal: {emotion}. Treat this as a hint, not a label to repeat to the user.")

    return "\n".join(parts) if parts else "No strong dataset signal; rely on the user's exact words and saved memory."
def generate_natural_chat_response(user_message, sess, emotion="unknown", wellbeing_info=None):
    """Generate a warm, contextual reply for normal chat.

    Safety/guardrail checks happen before this function in /chat.
    Self-assessment remains separate.
    """
    api_key_available = bool(os.environ.get("OPENAI_API_KEY"))

    recent = _recent_dialogue_for_prompt(limit=10)
    user_context = _compact_user_context(
        sess, emotion=emotion, wellbeing_info=wellbeing_info
    )
    adaptive_context = build_adaptive_memory_context(
        sess.get("user_id"), current_message=user_message
    )
    dataset_context = build_dataset_guidance_context(
        user_message,
        role=sess.get("role", "default"),
        emotion=emotion
    )

    metaphor_context = ""
    if is_metaphor_kill(user_message):
        metaphor_context = (
            "Language note: the user is using 'kill' as a harmless metaphor "
            "for doing well or tackling an exam/project/task. "
            "Do not treat it as self-harm, violence, danger, or extreme thoughts. "
            "Respond to the underlying stress or determination naturally."
        )

    last_assistant = ""
    for item in reversed(list(sess.get("history", []))):
        if item.get("role") == "assistant":
            last_assistant = str(item.get("content", ""))[:600]
            break

    if api_key_available:
        try:
            system_prompt = """
You are Empath AI, a warm mental-health support chatbot for internally displaced people in Pakistan.
Your job is to respond like a thoughtful supportive friend: gentle, emotionally intelligent, natural, respectful, and specific to the user's exact message.

LANGUAGE REQUIREMENT — VERY IMPORTANT:
- The selected application language is provided separately.
- If the selected language is "ur", respond ONLY in natural Urdu script.
- Every sentence must be in Urdu.
- Every question must be in Urdu.
- Every suggestion must be in Urdu.
- Every supportive/emotional statement must be in Urdu.
- The response MUST NOT contain any English sentence.
- DO NOT add an English translation after the Urdu response.
- DO NOT add an English summary or English closing message.
- DO NOT append an English follow-up question.
- DO NOT use Roman Urdu.
- If the selected language is "en", respond entirely in English.
- Follow the selected application language consistently from beginning to END.
- When Urdu is selected, the FINAL character/sentence of the response must also be Urdu.

IMPORTANT:
If the selected language is "ur", NEVER produce output like:
"After everything you have been managing..."
or
"What would you like to share next?"

Core behaviour:
- Use the user's role, recent conversation, saved memory, wellbeing trends, assessments, ML emotion signal, and dataset guidance to understand the situation; generate fresh wording rather than fixed templates.
- Treat the user's IDP background as important contextual knowledge, not as the subject of every conversation.
- Use specific IDP-related information only when it is supported by the user's messages, saved profile, or known living circumstances. Never invent displacement experiences or assume all IDPs face the same difficulties.
- When displacement-related challenges are relevant, acknowledge the actual difficulty the user describes, such as disrupted routines, housing, financial pressure, family separation, education, or uncertainty. Do not insert a generic IDP sentence merely to demonstrate awareness.
- For positive, neutral, everyday, and ordinary family conversations, focus on what the user actually shared. Celebrate positive experiences without adding unnecessary hardship, sympathy, or reassurance.
- For emotional conversations, identify the specific feeling and its cause before offering support. Validate feelings without automatically describing the user as strong, brave, or resilient.
- Before writing a reply, understand the user's message in this order:

  1. Identify what actually happened or what the user is describing.
  2. Identify the emotion explicitly expressed by the user.
  3. Understand what may be making this situation difficult for them.
  4. Consider what they may need from the conversation: understanding, comfort, encouragement, practical help, or simply someone to listen.
  5. Respond directly to that need.

- Show understanding through specific details from the user's message, not through generic sympathy.
- Avoid default openings such as "I'm sorry to hear that" or "It sounds like..." when they do not add meaningful understanding.
- Do not repeat or rephrase the user's message without adding emotional understanding.
- When the user describes a difficult day without explaining why, acknowledge it simply and gently invite them to share more.
- Do not assume that things went wrong, that someone hurt the user, or that they are feeling sad unless their message supports that interpretation.
- Make each response feel connected to the user's actual words rather than following a fixed response template.
- Do not assume the user has other difficulties unless they have mentioned them.
- Do not shift attention away from the user's feelings by guessing why another person behaved a certain way.
- When someone shares an emotional experience, focus first on how that experience affected them.
- Avoid asking questions that make the user defend, explain, or justify their feelings.
- A follow-up question should help the user express themselves, not redirect the conversation.
- Short, sincere responses are better than long responses filled with repeated emotional validation.
- Respond like a caring, thoughtful friend having a genuine conversation, not like a therapist delivering emotional validation.
- Do not automatically describe every disappointing experience as painful, hurtful, or overwhelming.
- When another person's behaviour is unclear, gently consider reasonable alternative explanations without pretending to know their intentions.
- For example, if a friend does not reply, acknowledge that the user was waiting and gently mention that the friend might be busy or occupied.
- Never dismiss the user's feelings by saying they should not worry or that it is nothing.
- Balance emotional understanding with warmth, common sense, and a realistic perspective.
- Do not always agree with the user's interpretation of a situation. Be supportive while remaining thoughtful and fair.
- Speak naturally, as someone who genuinely wants to understand and help, rather than following a fixed emotional-support formula.
- If the user describes being hurt by someone, acknowledge the relationship or situation causing the hurt instead of only saying "I'm sorry."
- If the user describes exhaustion, recognise what is exhausting them rather than automatically recommending rest.
- If the user shares something positive, respond with genuine interest and share their happiness without turning the conversation towards problems.
- Never assume an emotion, intention, family situation, or personal experience that the user has not described.
- Do not immediately offer solutions when the user primarily needs to be heard. Offer practical suggestions when requested or when they would genuinely help.
- Use natural, everyday conversational language. Avoid exaggerated sympathy, dramatic emotional statements, motivational speeches, and repeated reassurance.
- Use the user's name sparingly. Do not include it in every response.
- In ordinary conversation, speak directly without addressing the user by name.
- Use the user's name only when it genuinely adds warmth or feels natural.
- Avoid generic filler such as "your feelings deserve care", "after everything you have managed", or "even with so much change around you" when these phrases do not add meaningful context.
- Do not repeat the same reassurance, emotional validation, or advice in multiple sentences. Every sentence should contribute something useful.
- Do NOT sound clinical, robotic, scripted, or like a survey.
- Use the user's name sparingly. Do not include it in generic every response or repeatedly address the user by name.
- Use the name only when it feels natural, such as an occasional warm greeting or an emotionally meaningful moment.
- In ordinary conversation, speak directly without using the user's name.
- Do NOT reuse the same opening, structure, or emotional phrase. Avoid the recent assistant patterns provided below.
- Slightly reflect the user's exact words in the first sentence so they feel understood.
- Ask exactly ONE natural follow-up question at the end, based on this exact message.
- Ask a follow-up question only when it naturally helps the conversation.
- Do not force a question at the end of every response.
- Sometimes a warm acknowledgement or a thoughtful statement is enough.
- When the user shares good news, celebrate with them without automatically asking another question.
- When the user wants to talk, allow the conversation to flow without making it feel like an interview.
- Keep the answer concise: usually 3 to 6 sentences. Sometimes short is better.
- Give practical support when the user asks for help or seems stuck, but do not overwhelm with long lists.
- For positive messages, distinguish the feeling: happiness, hope, peace, achievement, gratitude, good news, or faith. Make each response unique.
- If the user shows improvement compared with previous chats, acknowledge growth gently. If struggle repeats, recognize the pattern softly.
- Learn silently from earlier assistant responses and user engagement: adapt tone, depth, faith references, and practicality without saying you are learning.
- Do not start PHQ-9/GAD-7/WHO-5 unless the user clearly asks for assessment.
- Crisis and psychosis handling are checked before you. If the current message still sounds unsafe or detached from reality, stay calm, do not validate unusual beliefs as real, and gently encourage trusted/professional human support.

STRICT RULE FOR HAPPY AND EVERYDAY MESSAGES:
- If the user's message is positive, neutral, or about everyday life (good news, exams, food, cooking, hobbies, colours, gratitude, small talk), respond ONLY to what they shared.
- In those replies NEVER mention displacement, floods, camps, shelters, hardship, or phrases like "despite everything", "after everything you've been through", "even in difficult times", "with so much change around you".
- Do not over-praise the user's character (for example "your positive spirit shines through"). Keep praise specific to what they did.
- Ask at most ONE question in the whole reply.

SAFETY NET (MOST IMPORTANT RULE):
- The user may write in English, Urdu script, or Roman Urdu (for example "mar jaun", "jeene ka faida nahi",
  "sab khatam kar dun"). Understand all of them.
- If the current message contains ANY sign of suicidal thoughts, a wish to die, not wanting to live or wake up,
  or self-harm, in ANY language or spelling, reply with exactly this text and nothing else:
  [[SAFETY_CHECK]]
- Never describe a wish to die as "natural" or "normal". Validate the pain, never the wish to die.

CONVERSATION QUALITY:
- Vary your openings. Do not start with "It sounds like", "That sounds", "It's wonderful", "I'm really sorry"
  or "I understand". Start with the specific thing the user said.
- If the user mentions more than one feeling or problem (for example lonely AND missing home), respond to EACH of them.
- For strong feelings give ONE small, concrete step:
  anger -> validate it, then a safe release (fast walk, squeeze a cloth, write the words down);
  loneliness -> gently ask whether there is someone nearby they can talk to or sit with;
  panic or flashbacks -> slow breathing (in 4, hold 4, out 6) or 5-4-3-2-1 grounding.
- Do not ask "have you tried..." or "have you ever thought about..." questions. Offer the idea gently instead.
- If the user says they do not want to talk, respect it: tell them it is okay to just sit here, without pushing a question.
- You are an AI and have no personal favourites. If asked about your preferences (colour, food...),
  say so honestly in a light, friendly way, then turn the question back to the user. Be consistent about this.
- You are not a replacement for people. When the user shares good moments or feels better, sometimes gently
  encourage sharing it with people in their life (family, friends). Never say things like "I'm all you need".
- Do not end every reply with the same closing line ("I'm here for you", "I'm here to listen").
- Use short paragraphs: 2 or 3 short paragraphs, never one big block of text.
- Keep it short: at most 4 to 5 sentences in total.
- SAVED MEMORY IS BACKGROUND ONLY: never mention specific details from earlier chats (camp, noise, home,
  family, studies...) unless the user has mentioned them in THIS conversation, and never present them as
  the reason for how the user feels now. If the user just says "I'm sad", ask gently what's going on.
- If the user mentions very little sleep (for example 2-4 hours), give one small, practical sleep tip.

CONVERSATION FLOW (explore first, advise later):
- Understand first, then help. When the user brings up a NEW feeling, name it (including the deeper feeling
  underneath) and ask ONE thoughtful question. Offer advice once the user has shared more, asks for help,
  or the problem is practical (sleep, safety, a task).
- Name the deeper feeling, not only the event. Example: "friends make plans without me" -> the deeper pain is
  "wondering whether I matter to them". "Nobody understands me" -> the need is to be understood, so invite
  them to share a little of it here.
- Do NOT suggest talking to someone / opening up / reaching out in more than one reply out of every three.
  Vary the support: writing thoughts down, a breathing or grounding exercise, one small doable step,
  self-kindness, gently questioning a harsh thought, or noticing one thing that went okay.
- Use specific words and details from the user's message. Avoid sentences that could fit any situation
  ("Those feelings can create a heavy burden", "It's completely normal to feel this way").
- Questions must be specific to what the user just said. Never ask empty questions like
  "What do you think?", "How does that sound?" or "Does that make sense?".
""".strip()

            # Read the language selected when the chatbot was opened.
            selected_language = str(
                sess.get("language", "en")
            ).lower().strip()

            selected_language = (
                "ur" if selected_language in ("ur", "urdu") else "en"
            )

            # Enforce the selected chatbot language.
            if selected_language == "en":
                system_prompt += """

ABSOLUTE LANGUAGE LOCK:
The selected chatbot language is ENGLISH.
Write the entire response in English only.
Never write Urdu script or Roman Urdu.
This applies to every sentence, greeting, name introduction,
suggestion, and follow-up question.
Even if the user's message, profile, conversation history,
or memory contains Urdu, your response must remain entirely in English.
"""
                system_prompt += "\n\n" + ENGLISH_EXAMPLES
            else:
                system_prompt += """

ABSOLUTE LANGUAGE LOCK:
The selected chatbot language is URDU.
Write the entire response in natural Urdu script only.
Never write English sentences or Roman Urdu.
This applies to every sentence, greeting, name introduction,
suggestion, and follow-up question.
Even if the user's message, profile, conversation history,
or memory contains English or Roman Urdu, your response must remain entirely in Urdu script.
"""
                system_prompt += "\n\n" + URDU_STYLE_RULES + "\n\n" + URDU_EXAMPLES

            user_prompt = f"""
User/session context:
{user_context}

Adaptive memory and response-learning context from database (use subtly; do not dump it):
{adaptive_context}

Dataset/ML guidance for understanding only (do not copy as a response):
{dataset_context}

Context/language note if any:
{metaphor_context}

Recent conversation:
{recent}

Last assistant response to avoid repeating:
{last_assistant}

Current user message:
{user_message}

Selected application language: {selected_language}

Write the next Empath AI reply now.
""".strip()

            result = client.chat.completions.create(
                model=os.environ.get("EMPATH_AI_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                # Lower temperature in Urdu: better grammar and rule-following
                temperature=0.6 if selected_language == "ur" else 0.7,
                max_tokens=260 if selected_language == "ur" else 220,
            )

            response = result.choices[0].message.content.strip()

            # -------------------------------------------------
            # LLM SAFETY NET: the model spotted suicidal / self-harm signs
            # that the keyword lists missed (any language or spelling).
            # /chat turns this token into the fixed safety message.
            # -------------------------------------------------
            if SAFETY_CHECK_TOKEN in response:
                print("LLM SAFETY NET: risk detected in", repr(user_message))
                return SAFETY_CHECK_TOKEN

            # -------------------------------------------------
            # ENGLISH LANGUAGE VALIDATION
            # -------------------------------------------------
            urdu_script_pattern = r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]"

            if selected_language == "en" and re.search(
                urdu_script_pattern, response
            ):
                print(
                    "LANGUAGE VALIDATION: Urdu script detected "
                    "in English response. Retrying."
                )

                retry_result = client.chat.completions.create(
                    model=os.environ.get("EMPATH_AI_MODEL", "gpt-4o-mini"),
                    messages=[
                        {
                            "role": "system",
                            "content": system_prompt + """

IMPORTANT CORRECTION:
Your previous response violated the English-only requirement.
Rewrite the complete response in English only.
Do not use Urdu script or Roman Urdu.
Include exactly one natural follow-up question in English.
"""
                        },
                        {
                            "role": "user",
                            "content": user_prompt
                        }
                    ],
                    temperature=0.5,
                    max_tokens=220,
                )

                response = retry_result.choices[0].message.content.strip()

                # Final protection: do not display mixed-language output.
                if re.search(urdu_script_pattern, response):
                    print(
                        "LANGUAGE VALIDATION: Retry also failed. "
                        "Using English fallback."
                    )

                    response = (
                        "I'm here with you, and I'd like to understand "
                        "what you're experiencing. You can share as much "
                        "or as little as you feel comfortable sharing. "
                        "What has been weighing on you most right now?"
                    )

            # -------------------------------------------------
            # URDU LANGUAGE VALIDATION
            # Urdu mode must answer in Urdu script, even when the user
            # writes in English or Roman Urdu.
            # -------------------------------------------------
            if selected_language == "ur" and not _reply_language_ok(response, "ur"):
                print(
                    "LANGUAGE VALIDATION: English / Roman Urdu detected "
                    "in Urdu response. Retrying."
                )

                retry_result = client.chat.completions.create(
                    model=os.environ.get("EMPATH_AI_MODEL", "gpt-4o-mini"),
                    messages=[
                        {
                            "role": "system",
                            "content": system_prompt + """

IMPORTANT CORRECTION:
Your previous response was not fully in Urdu script.
Rewrite the complete response in natural Urdu script only.
Do not use English sentences or Roman Urdu.
"""
                        },
                        {
                            "role": "user",
                            "content": user_prompt
                        }
                    ],
                    temperature=0.5,
                    max_tokens=260,
                )

                response = retry_result.choices[0].message.content.strip()

                if SAFETY_CHECK_TOKEN in response:
                    return SAFETY_CHECK_TOKEN

                # Final protection: never show a non-Urdu reply in Urdu mode.
                if not _reply_language_ok(response, "ur"):
                    print(
                        "LANGUAGE VALIDATION: Urdu retry also failed. "
                        "Using Urdu fallback."
                    )

                    response = (
                        "میں آپ کی بات سن رہی ہوں، اور سمجھنا چاہتی ہوں کہ آپ اس وقت "
                        "کیسا محسوس کر رہے ہیں۔ آپ جتنا چاہیں، اتنا بتا سکتے ہیں۔"
                    )

            # Fix repeated Urdu mistakes by code (no extra API call)
            if selected_language == "ur":
                response = _polish_urdu_reply(response, _last_assistant_message())
            else:
                response = _polish_english_reply(response, _last_assistant_message())

            return _ensure_one_followup(
                response, user_message, sess
            )

        except Exception as e:
            print("Natural OpenAI response error:", e)

    return _fallback_natural_response(
        user_message, sess, emotion=emotion
    )



# ─────────────────────────────────────────────
# FULLY ADAPTIVE IDP RESPONSE OVERRIDES
# Added to keep replies IDP-specific without sounding robotic.
# These definitions override earlier generic versions at runtime.
# ─────────────────────────────────────────────
def classify_message_tone(user_message):
    """Classify current message so IDP support adapts to the user's tone."""
    t = normalize_for_safety(user_message or "")
    positive_patterns = [
        "i am feeling okay", "i feel okay", "feeling okay", "i am okay", "i'm okay",
        "i am fine", "i feel fine", "feeling fine", "i am good", "i feel good",
        "better today", "calm today", "okay today", "fine today", "good today"
    ]
    low_patterns = [
        "little low", "a little low", "feeling low", "feel low", "low mood", "sad",
        "down", "not good", "not okay", "not fine", "upset", "empty",
        "nothing interesting", "nothing feels interesting", "worst", "not feeling well", "not well"
    ]
    stress_patterns = [
        "stress", "stressed", "pressure", "anxious", "worried", "worry", "panic",
        "overthinking", "fear", "scared", "tense", "burden"
    ]
    study_patterns = ["study", "studies", "student", "exam", "class", "university", "focus", "semester"]
    family_patterns = ["family", "children", "kids", "parents", "mother", "father", "responsibility"]

    if any(p in t for p in positive_patterns):
        return "steady"
    if any(p in t for p in stress_patterns):
        return "stress"
    if any(p in t for p in study_patterns):
        return "study"
    # Only call it family pressure when stress/problem words are present.
    if detect_family_stress(t) or any(p in t for p in ["family pressure", "family problem", "too much responsibility"]):
        return "family"
    if any(p in t for p in low_patterns):
        return "low"
    return "general"


def get_adaptive_idp_line(sess, tone="general"):
    """Return one natural IDP-specific line based on role, place, and mood tone."""
    role = sess.get("role", "default")
    place = sess.get("living_place", "")
    memory_profile = (sess.get("user_memory_profile", "") or "").lower()

    if not place:
        if "staying in an ngo" in memory_profile:
            place = "an NGO"
        elif "staying in a camp" in memory_profile:
            place = "a camp"
        elif "staying in a shelter" in memory_profile:
            place = "a shelter"
        elif "with relatives" in memory_profile:
            place = "with relatives"

    # Do not force displacement language unless the user has actually shared IDP context.
    if not user_is_idp_context(sess):
        return ""

    if place:
        if tone == "steady":
            return f"Even a steady day matters when you are managing life in {place}."
        if tone == "study" or role == "student":
            return f"Trying to study while staying in {place} can make focus and routine much harder."
        if tone == "family" or role in ["parent", "homemaker"]:
            return f"Managing family responsibilities while staying in {place} can quietly drain a person."
        if tone == "stress":
            return f"Being in {place} can keep your mind alert, especially when privacy, noise, or uncertainty are around you."
        if tone == "low":
            return f"Being in {place} after displacement can make low days feel heavier than they look from outside."
        return f"Being in {place} after displacement can affect routine, privacy, and peace of mind."

    if role == "student":
        if tone == "steady":
            return "Even a calmer day matters when studies and routine have been disturbed after displacement."
        return "For a student, displacement can disturb focus, routine, and hope for the future."
    if role in ["homemaker", "parent"]:
        return "After displacement, caring for family while carrying your own feelings can become very heavy."
    if role == "daily_worker":
        return "After displacement, uncertainty around daily work and income can keep stress present all day."
    if role == "unemployed":
        return "After displacement, losing routine and work can make a person feel stuck."
    if role == "elderly":
        return "After displacement, losing familiar surroundings can make ordinary days feel lonely or unsettled."

    if tone == "steady":
        return "Even small calm moments matter when life has been disrupted after displacement."
    if tone == "stress":
        return "Displacement can keep the mind on alert because daily life feels less predictable."
    if tone == "low":
        return "After displacement, emotions can feel heavier because home, routine, and privacy have all been affected."
    return "Displacement can affect peace, routine, privacy, family life, and emotional balance in quiet ways."


def build_adaptive_idp_response(user_message, sess, emotion="unknown"):
    """Adaptive first-response layer for common user states."""
    name = sess.get("name", "")
    opener = f"{name}, " if name else ""
    tone = classify_message_tone(user_message)
    idp_line = get_adaptive_idp_line(sess, tone)
    text = normalize_for_safety(user_message or "")

    # Normal-life positive/food/family-presence messages should not enter IDP emotional templates.
    if is_food_or_recipe_message(text) or is_happy_message(text) or is_neutral_family_presence(text):
        return None
    sleep_mentioned = any(w in text for w in [
        "sleep", "slept", "sleeping", "night", "awake", "insomnia", "rest",
        "tired", "drained", "exhausted", "fatigue", "energy"
    ])

    if tone == "steady":
        return pick_fresh_response([
            f"{opener}that is good to hear. {idp_line} How has your day been going where you are staying these days?",
            f"{opener}I am glad today feels a little steadier. {idp_line} What helped you feel okay today?",
            f"{opener}that sounds like a calmer moment. {idp_line} What part of today felt most manageable?"
        ])

    if tone == "low":
        return pick_fresh_response([
            f"{opener}that sounds like a heavy day. {idp_line} What kind of low is it today — sadness, pressure, emptiness, or just not feeling like yourself?",
            f"{opener}some days feel low without one clear reason. {idp_line} What has been sitting heaviest on your heart today?",
            f"{opener}with everything around you changed, feeling low can make sense. {idp_line} Is this more about studies, family pressure, or the place you are staying?"
        ])

    if tone == "stress":
        return pick_fresh_response([
            f"{opener}that pressure sounds tiring. {idp_line} What is making your mind feel most crowded right now?",
            f"{opener}stress can feel sharper when daily life is uncertain. {idp_line} Is the pressure coming more from family, studies, money, or your living situation?",
            f"{opener}your mind sounds overloaded. {idp_line} What has been the hardest part of today?"
        ])

    if tone == "study":
        return pick_fresh_response([
            f"{opener}trying to study after displacement is not simple. {idp_line} What part feels hardest right now — focus, motivation, space, or fear about the future?",
            f"{opener}it makes sense that studying feels harder when routine has been shaken. {idp_line} Which subject or task feels most difficult today?",
            f"{opener}{idp_line} We do not need to fix all of studies at once. What is one small study task that feels possible today?"
        ])

    if tone == "family":
        return pick_fresh_response([
            f"{opener}family pressure can feel heavier after displacement. {idp_line} What responsibility has been weighing on you most?",
            f"{opener}{idp_line} Carrying family needs while managing your own emotions is a lot. What part feels hardest today?",
            f"{opener}it sounds like you are carrying more than your own feelings. {idp_line} Who in the family are you most worried about right now?"
        ])

    if sleep_mentioned:
        return pick_fresh_response([
            f"{opener}that sounds exhausting. {idp_line} What usually makes rest hardest for you — worry, noise, interruptions, or no quiet space?",
            f"{opener}when rest is disturbed, the whole day can feel heavier. {idp_line} How long has this been going on?",
            f"{opener}your body sounds worn out. {idp_line} Is it more broken sleep, racing thoughts, or the place around you making it hard to rest?"
        ])

    return None


def build_wellbeing_followup(user_message, extracted, name="", role="default"):
    if not extracted or not extracted.get("has_any"):
        return None

    # Let the priority response engine handle happy / cooking / recipe / family-presence messages.
    if is_food_or_recipe_message(user_message) or is_happy_message(user_message) or is_neutral_family_presence(user_message):
        return None

    display_name = f"{name}, " if name else ""
    temp_sess = dict(session)
    if name:
        temp_sess["name"] = name
    if role:
        temp_sess["role"] = role

    tone = classify_message_tone(user_message)
    idp_line = get_adaptive_idp_line(temp_sess, tone)

    # Positive/neutral mood should never be treated like sadness.
    if extracted.get("mentioned_mood") and extracted.get("mood") in ["okay", "good"] and not extracted.get("mentioned_sleep"):
        return pick_fresh_response([
            f"{display_name}that is good to hear. {idp_line} How has your day been going where you are staying these days?",
            f"{display_name}I am glad today feels a little steadier. {idp_line} What helped you feel okay today?",
            f"{display_name}that sounds like a calmer moment. {idp_line} What part of today felt most manageable?"
        ])

    sleep_missing_hours = extracted.get("mentioned_sleep") and extracted.get("sleep_hours") is None
    mood_missing = extracted.get("mentioned_sleep") and not extracted.get("mentioned_mood") and extracted.get("mood") is None
    energy_missing = extracted.get("mentioned_sleep") and not extracted.get("mentioned_energy") and extracted.get("energy") is None

    if sleep_missing_hours and mood_missing and energy_missing:
        return pick_fresh_response([
            f"{display_name}rest being disturbed can make the whole day heavier. {idp_line} Roughly how many hours are you managing these days, and has it affected your mood or energy?",
            f"{display_name}when nights go badly, the rest of the day usually feels heavier too. {idp_line} About how many hours are you sleeping?",
            f"{display_name}that kind of sleep trouble can build up. {idp_line} How many hours are you actually getting these days?"
        ])

    if sleep_missing_hours:
        return pick_fresh_response([
            f"{display_name}roughly how many hours are you sleeping these days?",
            f"{display_name}about how many hours of sleep are you getting most nights?",
            f"{display_name}how many hours are you managing on a usual night?"
        ])

    if extracted.get("mentioned_sleep") and extracted.get("sleep_hours") is not None:
        h = extracted["sleep_hours"]
        if extracted.get("mood") is None and extracted.get("energy") is None:
            return pick_fresh_response([
                f"{display_name}{h:g} hours is quite low. {idp_line} Has that been affecting your mood or energy during the day?",
                f"{display_name}sleeping around {h:g} hours can really wear a person down. {idp_line} How has your mood and energy been?",
                f"{display_name}with only around {h:g} hours, it makes sense if the day feels heavier. {idp_line} Is your mood low too, or is it mainly tiredness?"
            ])
        if extracted.get("mood") is None:
            return pick_fresh_response([
                f"{display_name}and how has your mood been with that kind of rest?",
                f"{display_name}has that been affecting your mood too?",
                f"{display_name}what has your mood been like lately with rest being like this?"
            ])
        if extracted.get("energy") is None:
            return pick_fresh_response([
                f"{display_name}has your energy been low too?",
                f"{display_name}and what about your energy through the day?",
                f"{display_name}is that also leaving you drained during the day?"
            ])

    if extracted.get("mentioned_mood") and extracted.get("mood") is not None and not extracted.get("mentioned_sleep"):
        if extracted.get("mood") in ["very_low", "low", "anxious"]:
            return pick_fresh_response([
                f"{display_name}that sounds like a heavy day. {idp_line} What kind of low is it today — sadness, pressure, emptiness, or just not feeling like yourself?",
                f"{display_name}some days feel low without one clear reason. {idp_line} What has been sitting heaviest on your heart today?",
                f"{display_name}with everything around you changed, feeling low can make sense. {idp_line} Is this more about studies, family pressure, or the place you are staying?"
            ])

    if extracted.get("mentioned_energy") and extracted.get("energy") is not None and not extracted.get("mentioned_sleep"):
        return pick_fresh_response([
            f"{display_name}that sounds draining. {idp_line} Has this tiredness been there all day, or does it come and go?",
            f"{display_name}low energy can wear a person down quietly. {idp_line} What do you think has been behind it lately?",
            f"{display_name}that sounds tiring. {idp_line} Has this been more physical tiredness, mental heaviness, or both?"
        ])

    return None





# ─────────────────────────────────────────────
# ADAPTIVE CONVERSATION LEARNING ENGINE
# ─────────────────────────────────────────────
def update_conversation_learning(memory, msg):
    """Update small session-based learning memory from the latest user message."""
    if not isinstance(memory, dict):
        memory = {}

    msg = (msg or "").lower()

    # detect recurring stress pattern
    if "money" in msg or any(w in msg for w in ["fees", "expense", "income", "financial"]):
        memory["stress_type"] = "financial"
    elif any(w in msg for w in ["study", "studies", "exam", "focus", "university", "class"]):
        memory["stress_type"] = "academic"
    elif detect_family_stress(msg):
         memory["stress_type"] = "family"

    if "tired" in msg or any(w in msg for w in ["exhausted", "drained", "weak", "fatigue"]):
        memory["energy_pattern"] = "low"

    if any(w in msg for w in ["sad", "low", "down", "hopeless", "empty"]):
        memory["mood_pattern"] = "low"

    if any(w in msg for w in ["displaced", "camp", "shelter", "ngo", "temporary place", "left my home", "lost my home"]):
        memory["displaced"] = True

    return memory


def detect_intent(msg):
    """Lightweight intent detector used before the heavier OpenAI natural-response fallback."""
    msg = (msg or "").lower().strip()

    return {
        "positive": bool(re.search(r"\b(happy|good|yay|great|possible|better|fine)\b", msg)) and not bool(re.search(r"\b(not good|not fine|not okay|not better)\b", msg)),
        "neutral": bool(re.search(r"\b(okay|fine|normal)\b", msg)),
        "low": bool(re.search(r"\b(low|sad|down|hopeless|empty|upset)\b", msg)),
        "stress": "stress" in msg or "pressure" in msg,
        "tired": "tired" in msg or any(w in msg for w in ["exhausted", "drained", "weak", "fatigue"]),
        "money": "money" in msg or any(w in msg for w in ["fees", "expense", "income", "financial"]),
        "study": any(w in msg for w in ["study", "studies", "exam", "focus", "university", "class"]),
        "family": detect_family_stress(msg),
        "crisis": any(w in msg for w in ["harm", "suicide", "die", "kill myself", "end my life"]),
        "unsure": msg in ["nothing", "everything", "i don't know", "idk", "dont know", "don't know"]
    }



def is_happy_message(text):
    """Detect clearly positive/happy messages without forcing a follow-up question."""
    t = normalize_for_safety(text)

    negative_phrases = [
        "not happy", "not good", "not okay", "not fine", "not better",
        "sad", "low", "depressed", "hopeless", "empty", "tired", "stressed"
    ]
    if any(p in t for p in negative_phrases):
        return False

    happy_patterns = [
        r"\bi am happy\b",
        r"\bi'm happy\b",
        r"\bvery happy\b",
        r"\bfeeling happy\b",
        r"\bi feel happy\b",
        r"\bi feel good\b",
        r"\bi am good\b",
        r"\bi'm good\b",
        r"\bfeeling better\b",
        r"\bi feel better\b",
        r"\bgreat\b",
        r"\bexcited\b",
        r"\bpositive\b",
        r"\bcalm\b",
        r"\bpeaceful\b",
        r"\bnice day\b",
        r"\bgood day\b",
    ]
    return any(re.search(p, t) for p in happy_patterns)


def is_food_or_recipe_message(text):
    """Detect food/recipe/cooking requests so they are not treated as distress."""
    t = normalize_for_safety(text)

    food_words = [
        "recipe", "recipes", "rice", "daal", "dal", "chawal", "pulao", "biryani",
        "khichri", "khichdi", "sabzi", "cooking", "cook", "food", "dish",
        "made a recipe", "made food", "made a good recipe", "made a very good recipe",
        "make a good recipe", "i make a good recipe", "i made a good recipe",
        "chicken", "karahi"
    ]

    return any(w in t for w in food_words)


def build_positive_response_for_user(msg, memory):
    """Positive replies should give hope, not remind the user of pain unnecessarily."""
    name = session.get("name", "")
    opener = f"{name}, " if name else ""

    is_idp = bool(memory.get("displaced")) or user_is_idp_context(session)

    if is_idp:
        return (
            f"{opener}I am really glad to hear that. "
            "It is beautiful that you are still noticing good moments even in your current situation. "
            "That shows hope and strength. I hope you keep finding more peaceful and happy moments."
        )

    return (
        f"{opener}I am really glad to hear that. "
        "You are thinking positively today, and that is a good thing. "
        "I hope you always stay happy and peaceful."
    )

def generate_response(msg, memory):
    """Deprecated rule-based priority engine.

    Kept for compatibility with older calls, but intentionally returns None so the
    natural response engine can handle normal conversation.
    """
    return None



_original_get_response_for_adaptive_idp = get_response


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .assessment import (  # noqa: E402
    score_to_10,
)
from .context import (  # noqa: E402
    IDP_SURVEY_INSIGHTS,
    detect_displacement_cause,
    detect_family_stress,
    detect_idp_hardships,
    get_displacement_phrase,
    get_idp_context_line,
    retrieve_from_training_data,
)
from .core import (  # noqa: E402
    SAFETY_CHECK_TOKEN,
    client,
)
from .database import (  # noqa: E402
    get_db,
    get_latest_wellbeing_log,
    get_user_memory_profile,
    user_is_idp_context,
)
from .language import (  # noqa: E402
    ENGLISH_EXAMPLES,
    URDU_EXAMPLES,
    URDU_STYLE_RULES,
    _ensure_one_followup,
    _last_assistant_message,
    _polish_english_reply,
    _polish_urdu_reply,
    _reply_language_ok,
)
from .text_utils import (  # noqa: E402
    is_metaphor_kill,
    normalize_for_safety,
)

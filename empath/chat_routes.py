"""Chat routes: /app, /init, /chat, /reset, chat restore, welcome messages.

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


@app.route("/app")
@login_required
def chat_app():
    session_name = session.get("account_name", "")
    crisis_transition = request.args.get("crisis") == "1"

    account_id = session.get("account_id")
    account_name = session.get("account_name")
    language = session.get("language", "en")

    # Get the same Urdu name used by dashboard/assessment
    urdu_name = get_urdu_name_for_account(account_id)

    # Keep the current conversation when the user comes back to the chat page
    # (e.g. after visiting Words of Hope). Only a crisis hand-off or a different
    # account starts a fresh chat; the "New Chat" button uses /reset.
    if crisis_transition or session.get("chat_account_id") != account_id:
        _clear_chat_state()

    session.permanent = True
    session["account_id"] = account_id
    session["account_name"] = account_name
    session["language"] = language

    if crisis_transition:
        session["crisis_transition_pending"] = True

    return render_template(
        "index.html",
        user_name=session_name,
        user_name_urdu=urdu_name
    )
@app.route("/chat", methods=["GET"])
@login_required
def chat_page_redirect():
    if request.args.get("crisis") == "1":
        return redirect(url_for("chat_app", crisis=1))
    return redirect(url_for("chat_app"))

@app.route("/init", methods=["GET"])
def init():
    language = request.args.get("language", session.get("language", "en")).lower()

    if language not in ("en", "ur"):
        language = "en"

    session["language"] = language

    account_id = session.get("account_id")
    account_name = (session.get("account_name") or "").strip()
    

    init_db()

    # ── Returning to an ongoing chat (e.g. back from Words of Hope) ──
    # Send the existing messages back instead of starting over.
    if (
        account_id
        and session.get("chat_account_id") == account_id
        and session.get("user_id")
        and session.get("stage")
        and not session.get("crisis_transition_pending")
    ):
        restored = _get_current_chat_messages()
        if any(m["role"] == "user" for m in restored):
            first_name = (account_name.split()[0].capitalize() if account_name else "")
            return jsonify({
                "restore": True,
                "history": restored,
                "response": session.get("chat_welcome_message", ""),
                "stage": session.get("stage"),
                "name": session.get("name", first_name),
                "name_urdu": get_urdu_name_for_account(account_id),
                "is_returning_user": session.get("is_returning_user", False),
                "type": "normal"
            })

    if not account_id or not account_name:
        init_session()

        if language == "ur":
            welcome = "السلام علیکم۔ میں آپ کی بات سننے اور مدد کرنے کے لیے یہاں ہوں۔ آپ کو کس نام سے پکاروں؟"
        else:
            welcome = "Hello. I am here to listen and support you. What should I call you?"

        add_to_history("assistant", welcome)

        return jsonify({
            "response": welcome,
            "stage": session.get("stage"),
            "name": session.get("name", ""),
            "name_urdu": "",
            "is_returning_user": False,
            "type": "normal"
        })

    session.permanent = True
    session["account_id"] = account_id
    session["account_name"] = account_name

    # Set by /reset when the user pressed "New Chat"
    is_new_chat = bool(session.pop("new_chat_requested", False))

    init_session()

    first_name = account_name.split()[0].capitalize()
    session["name"] = first_name

    linked_user = get_user_by_account_id(account_id)

    if not linked_user:
        linked_user = find_existing_user(first_name)
        if linked_user and users_has_account_id() and not linked_user["account_id"]:
            try:
                db = get_db()
                db.execute("UPDATE users SET account_id = ? WHERE id = ?", (account_id, linked_user["id"]))
                db.commit()
                linked_user = get_user_by_id(linked_user["id"])
            except Exception:
                pass

    if linked_user:
        session["user_id"] = linked_user["id"]
        session["role"] = linked_user["role"] if linked_user["role"] else "default"
        session["living_place"] = get_row_value(linked_user, "living_place", "")
        session["user_memory_profile"] = get_user_memory_profile(linked_user["id"])
        update_user_last_seen(linked_user["id"])
        load_recent_self_assessment(linked_user["id"])

        real_history = has_real_user_history(linked_user)

        if real_history:
            session["is_returning_user"] = True
            session["stage"] = STAGE_LISTEN

            # Different welcome for "New Chat" vs coming back after logout/login
            welcome_name = first_name
            if language == "ur":
                urdu_full_name = get_urdu_name_for_account(account_id) or ""
                welcome_name = urdu_full_name.split()[0] if urdu_full_name.strip() else first_name

            welcome = build_returning_welcome(
                welcome_name,
                language,
                linked_user["id"],
                is_new_chat=is_new_chat
            )

        else:
            session["is_returning_user"] = False
            session["stage"] = STAGE_ROLE

            if language == "ur":
                welcome = (
                    f"ہیلو، {first_name}۔ آپ سے مل کر خوشی ہوئی۔ "
                    f"کیا آپ اپنے بارے میں تھوڑا بتا سکتے ہیں — "
                    f"کیا آپ طالب علم ہیں، ملازمت کرتے ہیں، گھریلو خاتون ہیں، "
                    f"بے روزگار ہیں، یا کچھ اور؟"
                )
            else:
                welcome = (
                    f"Hello, {first_name}. It's nice to meet you. "
                    f"Can you tell me a little about yourself — are you a student, working, "
                    f"a housewife/homemaker, unemployed, or something else?"
                )

    else:
        new_user_id = create_user(first_name, account_id=account_id)
        session["user_id"] = new_user_id
        session["is_returning_user"] = False
        session["role"] = "default"
        load_recent_self_assessment(new_user_id)

        session["stage"] = STAGE_ROLE

        if language == "ur":
            welcome = (
                f"ہیلو، {first_name}۔ آپ سے مل کر خوشی ہوئی۔ "
                f"کیا آپ اپنے بارے میں تھوڑا بتا سکتے ہیں — "
                f"کیا آپ طالب علم ہیں، ملازمت کرتے ہیں، گھریلو خاتون ہیں، "
                f"بے روزگار ہیں، یا کچھ اور؟"
            )
        else:
            welcome = (
                f"Hello, {first_name}. It's nice to meet you. "
                f"Can you tell me a little about yourself — are you a student, working, "
                f"a housewife/homemaker, unemployed, or something else?"
            )

    if session.get("crisis_transition_pending"):
        session["crisis_transition_pending"] = False
        session["stage"] = STAGE_LISTEN
        session["is_returning_user"] = True
        welcome = get_crisis_support_message(session.get("name", first_name))

    add_to_history("assistant", welcome)

    # Remember where this conversation starts, so it can be restored later
    _mark_chat_start(account_id, welcome)

    return jsonify({
        "response": welcome,
        "stage": session.get("stage"),
        "name": session.get("name", first_name),
        "name_urdu": get_urdu_name_for_account(account_id) if account_id else "",
        "is_returning_user": session.get("is_returning_user", False),
        "type": "normal"
    })


# ─────────────────────────────────────────────
# KEEP / RESTORE THE CURRENT CONVERSATION
# The session cookie is too small for the whole chat, so the chat is read back
# from the chat_history table (every message is already saved there).
# ─────────────────────────────────────────────
CHAT_STATE_KEEP_KEYS = ("account_id", "account_name", "language", "_permanent")


def _clear_chat_state():
    """Forget the current conversation but stay logged in."""
    for key in list(session.keys()):
        if key not in CHAT_STATE_KEEP_KEYS:
            session.pop(key, None)


def _mark_chat_start(account_id, welcome):
    """Store the id of the last saved message, so only this chat's messages are restored."""
    session["chat_account_id"] = account_id
    session["chat_welcome_message"] = welcome
    session["chat_start_msg_id"] = 0

    user_id = session.get("user_id")
    if not user_id:
        return
    try:
        db = get_db()
        row = db.execute(
            "SELECT MAX(id) AS max_id FROM chat_history WHERE user_id = ?",
            (user_id,)
        ).fetchone()
        session["chat_start_msg_id"] = (row["max_id"] or 0) if row else 0
    except Exception as e:
        print("Chat start marker error:", e)


def _get_current_chat_messages(limit=200):
    """Messages of the current conversation, oldest first, for the chat page."""
    user_id = session.get("user_id")
    if not user_id:
        return []
    try:
        db = get_db()
        rows = db.execute(
            """
            SELECT sender, message, timestamp
            FROM chat_history
            WHERE user_id = ? AND id > ?
            ORDER BY id ASC
            LIMIT ?
            """,
            (user_id, session.get("chat_start_msg_id", 0), limit)
        ).fetchall()
    except Exception as e:
        print("Chat restore error:", e)
        return []

    return [
        {
            "role": "user" if r["sender"] == "user" else "bot",
            "text": r["message"],
            # SQLite CURRENT_TIMESTAMP is UTC; the browser converts it to local time
            "time": str(r["timestamp"] or ""),
        }
        for r in rows
    ]


@app.route("/reset", methods=["POST"])
@login_required
def reset_chat():
    """'New Chat' button: start a fresh conversation, stay logged in."""
    _clear_chat_state()
    session["new_chat_requested"] = True
    return jsonify({"status": "reset"})


# ─────────────────────────────────────────────
# WELCOME MESSAGES: New Chat vs. coming back after logout/login
# ─────────────────────────────────────────────
def _time_since_last_chat(user_id):
    """timedelta since the user's last saved message, or None."""
    row = get_last_chat_message(user_id)
    if not row or not row["timestamp"]:
        return None
    try:
        last = datetime.strptime(str(row["timestamp"])[:19], "%Y-%m-%d %H:%M:%S")
        # SQLite CURRENT_TIMESTAMP is UTC
        delta = datetime.utcnow() - last
        return delta if delta.total_seconds() >= 0 else None
    except Exception as e:
        print("Last chat time error:", e)
        return None


def _humanize_time_ago(delta, language="en"):
    """'about 5 hours ago' / 'تقریباً 5 گھنٹے پہلے' style text."""
    minutes = int(delta.total_seconds() // 60)
    hours = minutes // 60
    days = hours // 24

    if language == "ur":
        if minutes < 10:
            return "ابھی کچھ دیر پہلے"
        if minutes < 60:
            return f"تقریباً {minutes} منٹ پہلے"
        if hours == 1:
            return "تقریباً ایک گھنٹہ پہلے"
        if hours < 24:
            return f"تقریباً {hours} گھنٹے پہلے"
        if days == 1:
            return "کل"
        if days < 7:
            return f"{days} دن پہلے"
        if days < 14:
            return "پچھلے ہفتے"
        return "کافی دن پہلے"

    if minutes < 10:
        return "just a little while ago"
    if minutes < 60:
        return f"about {minutes} minutes ago"
    if hours == 1:
        return "about an hour ago"
    if hours < 24:
        return f"about {hours} hours ago"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    if days < 14:
        return "last week"
    return "a while ago"


def _last_chat_was_crisis(user_id):
    """True if the saved risk pattern says the last conversations reached crisis level."""
    try:
        row = get_db().execute(
            "SELECT risk_pattern FROM user_memory WHERE user_id = ?", (user_id,)
        ).fetchone()
        return bool(row and (row["risk_pattern"] or "") in ("crisis", "emergency"))
    except Exception:
        return False


def build_returning_welcome(first_name, language, user_id, is_new_chat=False):
    """Welcome for a user who has chatted before.

    - New Chat button  -> a fresh-start message (no time, they were just here)
    - Logout -> login  -> "we last talked about 5 hours ago, how are you feeling today?"
    - Last chat was a crisis and it was recent -> a gentle safety check-in
    """
    if is_new_chat:
        if language == "ur":
            return random.choice([
                f"چلیں {first_name}، ایک نئی شروعات کرتے ہیں۔ اس وقت آپ کے ذہن میں کیا چل رہا ہے؟",
                "نئی بات چیت، نیا آغاز۔ آج آپ کس بارے میں بات کرنا چاہیں گے؟",
                f"{first_name}، میں سن رہی ہوں۔ جو بھی دل میں ہو، یہاں سے شروع کر سکتے ہیں۔",
            ])
        return random.choice([
            f"Let's start fresh, {first_name}. What's on your mind right now?",
            "A new conversation, a clean page. What would you like to talk about?",
            f"I'm listening, {first_name}. Start wherever feels easiest.",
        ])

    delta = _time_since_last_chat(user_id)

    if delta is None:
        if language == "ur":
            return f"خوش آمدید، {first_name}۔ آج آپ کیسا محسوس کر رہے ہیں؟"
        return f"Welcome back, {first_name}. How are you feeling today?"

    ago = _humanize_time_ago(delta, language)

    # Gentle safety check-in if the last chat was a crisis in the past week
    if _last_chat_was_crisis(user_id) and delta.days < 7:
        if language == "ur":
            return (
                f"خوش آمدید، {first_name}۔ ہماری آخری بات {ago} ہوئی تھی، اور اس وقت حالات بہت بھاری محسوس ہو رہے تھے۔ "
                "اب آپ کیسا محسوس کر رہے ہیں؟ کیا آج آپ محفوظ ہیں؟"
            )
        return (
            f"Welcome back, {first_name}. We last talked {ago}, and things felt very heavy then. "
            "How are you feeling now? Are you safe today?"
        )

    if language == "ur":
        return random.choice([
            f"خوش آمدید، {first_name}۔ ہماری آخری بات {ago} ہوئی تھی۔ آج آپ کیسا محسوس کر رہے ہیں؟",
            f"آپ کو دوبارہ دیکھ کر اچھا لگا، {first_name}۔ ہم نے {ago} بات کی تھی۔ اب دل کا کیا حال ہے؟",
        ])
    return random.choice([
        f"Welcome back, {first_name}. We last talked {ago}. How are you feeling today?",
        f"Good to see you again, {first_name}. It's been {ago.replace(' ago', '')} since we talked. How are things feeling now?"
        if ago.endswith(" ago") else
        f"Good to see you again, {first_name}. We last talked {ago}. How are things feeling now?",
    ])


# CHAT SAFETY FAIL-SAFE HELPERS
# Only protects /chat safety routing; normal flow remains unchanged.
def _crisis_plain_fallback(name=""):
    display_name = f"{name}, " if name else ""
    return (
        f"{display_name}I can hear this is a very dangerous and painful moment. "
        "Right now, your safety matters more than anything else. Please do not stay alone; sit near someone you trust or call them now. "
        "You can also call 1122 for emergency help or Umang at 0311-7786264 for immediate support. "
        "Can you move close to another person right now?"
    )

def _safe_record_user_message(user_message):
    try:
        add_to_history("user", user_message)
    except Exception as e:
        print("Safe user history record error:", e)
    try:
        if session.get("user_id"):
            save_chat_message(session["user_id"], "user", user_message)
    except Exception as e:
        print("Safe user DB record error:", e)

def _safe_return_chat_response(response, response_type="normal", stage=None):
    try:
        add_to_history("assistant", response)
    except Exception as e:
        print("Safe assistant history record error:", e)
    try:
        if session.get("user_id"):
            save_chat_message(session["user_id"], "assistant", response)
    except Exception as e:
        print("Safe assistant DB record error:", e)

    payload = {"response": response, "type": response_type}
    if stage is not None:
        payload["stage"] = stage
    return jsonify(payload)

def _early_safety_response_if_needed(user_message):
    """Return urgent safety response before DB/OpenAI work."""
    try:
        safety = run_safety_checks(user_message)
    except Exception as e:
        print("Early safety check error:", e)
        norm = normalize_for_safety(user_message)
        if direct_crisis_override(norm):
            _safe_record_user_message(user_message)
            response = _crisis_plain_fallback(session.get("name", ""))
            session["crisis_active"] = True
            session["crisis_level"] = "emergency"
            return _safe_return_chat_response(response, "emergency", session.get("stage"))
        return None

    stype = safety.get("type")
    if stype not in {"blocked", "grief_dream", "psychosis", "special_support", "emergency", "crisis"}:
        return None

    _safe_record_user_message(user_message)

    try:
        if stype in ["emergency", "crisis"]:
            session["crisis_active"] = True
            session["crisis_level"] = safety.get("level", "crisis")

            # Crisis messages are part of the conversation mood too
            try:
                update_words_of_hope_mood(user_message, force_mood="sad")
            except Exception as e:
                print("Words of Hope mood update (crisis) error:", e)

            # ...and of the Wellness page: a crisis message is a very low mood
            # (plus any sleep / energy the user mentioned in it).
            try:
                if session.get("user_id"):
                    info = save_wellbeing_if_mentioned(session["user_id"], user_message, mood_if_missing="very_low")
                    print("Wellness detected (crisis):", info)
            except Exception as e:
                print("Wellbeing save (crisis) error:", e)

            try:
                response = build_crisis_response(user_message, session.get("name", ""))
            except Exception as e:
                print("Crisis response builder error:", e)
                response = _crisis_plain_fallback(session.get("name", ""))
            return _safe_return_chat_response(response, stype, session.get("stage"))

        if stype == "blocked":
            return _safe_return_chat_response(safety.get("response", "I care about your wellbeing, so I cannot help with that. But I can listen to what you are going through right now."), "blocked", session.get("stage"))

        if stype == "grief_dream":
            response = build_grief_dream_support_response(user_message, session.get("name", ""), session.get("role", "default"))
            return _safe_return_chat_response(response, "grief_dream", session.get("stage"))

        if stype == "psychosis":
            response = build_psychosis_support_response(user_message, session.get("name", ""), session.get("role", "default"))
            return _safe_return_chat_response(response, "psychosis", session.get("stage"))

        if stype == "special_support":
            return _safe_return_chat_response(safety.get("response", "That sounds really unsettling, and I am glad you told me. You do not have to carry this alone. Is someone you trust nearby right now?"), "special_support", session.get("stage"))

    except Exception as e:
        print("Early safety route error:", e)
        response = _crisis_plain_fallback(session.get("name", ""))
        session["crisis_active"] = True
        session["crisis_level"] = "crisis"
        return _safe_return_chat_response(response, "crisis", session.get("stage"))

    return None

def _llm_safety_flag_response(user_message):
    """The LLM flagged suicide / self-harm risk: switch to crisis mode and send the fixed safety message."""
    session["crisis_active"] = True
    session["crisis_level"] = "crisis"
    response = build_crisis_response(user_message, session.get("name", ""))

    add_to_history("assistant", response)
    if session.get("user_id"):
        save_chat_message(session["user_id"], "assistant", response)
        try:
            update_user_memory_profile(session["user_id"], user_message, session.get("emotion", "unknown"), "crisis")
        except Exception as e:
            print("Memory update after LLM safety flag error:", e)

    return jsonify({"response": response, "type": "crisis", "stage": session.get("stage")})


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json or {}

    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({"response": "I did not catch that. Could you say that again?"})

      # Keep the language selected when the user opened the chatbot.
    language = str(
        session.get("language")
        or data.get("language")
        or "en"
    ).lower().strip()

    if language in ("urdu", "ur"):
        language = "ur"
    else:
        language = "en"

    session["language"] = language
    session["sa_language"] = language

    # CRITICAL: urgent safety messages must never depend on DB writes or OpenAI/API calls.

    if not user_message:
        return jsonify({"response": "I did not catch that. Could you say that again?"})

    # CRITICAL: urgent safety messages must never depend on DB writes or OpenAI/API calls.
    early_safety_response = _early_safety_response_if_needed(user_message)
    if early_safety_response is not None:
        return early_safety_response

    if "stage" not in session:
        init_db()
        init_session()
        if session.get("account_id") and session.get("account_name"):
            account_id = session.get("account_id")
            first_name = session.get("account_name", "").split()[0].capitalize() if session.get("account_name") else ""
            session["name"] = first_name
            linked_user = get_user_by_account_id(account_id)
            if linked_user:
                session["user_id"] = linked_user["id"]
                session["is_returning_user"] = True
                session["role"] = linked_user["role"] if linked_user["role"] else "default"
                session["living_place"] = get_row_value(linked_user, "living_place", "")
                session["user_memory_profile"] = get_user_memory_profile(linked_user["id"])
                update_user_last_seen(linked_user["id"])
                load_recent_self_assessment(linked_user["id"])
                session["stage"] = STAGE_LISTEN
            elif first_name:
                new_user_id = create_user(first_name, account_id=account_id)
                session["user_id"] = new_user_id
                session["is_returning_user"] = False
                session["role"] = "default"
                session["stage"] = STAGE_ROLE

    stage = session.get("stage", STAGE_NAME)
    add_to_history("user", user_message)

    # Save user message to DB if known
    if session.get("user_id"):
        save_chat_message(session["user_id"], "user", user_message)

    # Save wellbeing only if naturally mentioned
        # Save wellbeing only if naturally mentioned
        # Save wellbeing only if naturally mentioned
    wellbeing_info = None
    if session.get("user_id"):
        wellbeing_info = save_wellbeing_if_mentioned(
            session["user_id"],
            user_message
        )

    if wellbeing_info:
        print("Wellness detected:", wellbeing_info)

        user = get_user_by_id(session["user_id"])
        gnuhealth_id = user["gnuhealth_id"] if user else None

        
        if gnuhealth_id:
            # Save wellness directly to this user's local MyGNUHealth profile.
            tracker_result = update_mygnuhealth_tracker(
                gnuhealth_id,
                wellbeing_info.get("sleep_hours"),
                wellbeing_info.get("mood"),
                wellbeing_info.get("energy")
            )

            print("MyGNUHealth tracker result:", tracker_result)

        else:
            print("No MyGNUHealth profile ID linked to this user.")

            
    # Learn compact user profile from this message, not the full chat.
    if session.get("user_id"):
        try:
            update_user_memory_profile(session["user_id"], user_message, session.get("emotion", "unknown"), session.get("crisis_level", "safe"))
            session["user_memory_profile"] = get_user_memory_profile(session["user_id"])
        except Exception as e:
            print("User memory update error:", e)

    # Save displacement living-place memory when user mentions it.
    detected_place = detect_living_place(user_message)
    if detected_place:
        session["living_place"] = detected_place
        if session.get("user_id"):
            update_user_living_place(session["user_id"], detected_place)

    safety = run_safety_checks(user_message)

    if safety["type"] in ["emergency", "crisis"]:
        session["crisis_active"] = True
        session["crisis_level"] = safety.get("level", "crisis")

    if safety["type"] == "blocked":
        response = safety["response"]
        add_to_history("assistant", response)
        if session.get("user_id"):
            save_chat_message(session["user_id"], "assistant", response)
        return jsonify({"response": response, "type": "blocked"})

    if safety["type"] == "grief_dream":
        response = build_grief_dream_support_response(
            user_message,
            session.get("name", ""),
            session.get("role", "default")
        )
        add_to_history("assistant", response)
        if session.get("user_id"):
            save_chat_message(session["user_id"], "assistant", response)
        return jsonify({"response": response, "type": "grief_dream"})

    if safety["type"] == "psychosis":
        response = build_psychosis_support_response(
            user_message,
            session.get("name", ""),
            session.get("role", "default")
        )
        add_to_history("assistant", response)
        if session.get("user_id"):
            save_chat_message(session["user_id"], "assistant", response)
        return jsonify({"response": response, "type": "psychosis"})

    if safety["type"] == "special_support":
        response = safety["response"]
        add_to_history("assistant", response)
        if session.get("user_id"):
            save_chat_message(session["user_id"], "assistant", response)
        return jsonify({"response": response, "type": "special_support"})

    if safety["type"] == "emergency":
        response = build_crisis_response(user_message, session.get("name", ""))
        add_to_history("assistant", response)
        if session.get("user_id"):
            save_chat_message(session["user_id"], "assistant", response)
        return jsonify({"response": response, "type": "emergency"})

    if safety["type"] == "crisis":
        response = build_crisis_response(user_message, session.get("name", ""))
        add_to_history("assistant", response)
        if session.get("user_id"):
            save_chat_message(session["user_id"], "assistant", response)
        return jsonify({"response": response, "type": "crisis"})
    
    crisis_level = session.get("crisis_level", safety.get("level", "safe"))

    emotion, conf = detect_emotion(user_message)

    # Separate mood tracking for Words of Hope only
    
    # Mood follows the whole conversation (recent messages weigh more)
    # and is saved to the DB so the Words of Hope page still knows it later.
    session["words_of_hope_mood"] = update_words_of_hope_mood(
        user_message,
        wellbeing_info,
        emotion
    )

    print("WOH DETECTED MOOD:", session.get("words_of_hope_mood"))

    # Keep original chatbot emotion unchanged
    session["emotion"] = emotion
    session["confidence"] = conf
    session["is_positive"] = is_positive_message(user_message)
    session["idp_hardships"] = detect_idp_hardships(session.get("history", []))

    session["last_user_message"] = user_message

    if session.get("crisis_active", False):
        response = get_crisis_followup_response(session.get("name", ""), user_message)
        add_to_history("assistant", response)
        if session.get("user_id"):
            save_chat_message(session["user_id"], "assistant", response)
        return jsonify({"response": response, "type": "crisis", "stage": session.get("stage")})

    # Deep hopelessness ("nothing will ever get better", "I give up") is a warning sign:
    # reflect it, gently ask about thoughts of suicide, share the helpline once,
    # and stay in safety mode for the next few messages.
    if is_hopeless_message(user_message):
        response = get_hopelessness_check_response(session.get("name", ""), user_message)
        add_to_history("assistant", response)
        if session.get("user_id"):
            save_chat_message(session["user_id"], "assistant", response)
        return jsonify({"response": response, "type": "crisis", "stage": session.get("stage")})

    response = ""

    if stage == STAGE_NAME:
        if looks_like_name(user_message):
            name = user_message.split()[0].capitalize()
            session["name"] = name

            account_id = session.get("account_id")
            existing_user = get_user_by_account_id(account_id) if account_id else find_existing_user(name)

            if existing_user:
                session["user_id"] = existing_user["id"]
                session["is_returning_user"] = True
                session["role"] = existing_user["role"] if existing_user["role"] else "default"
                session["living_place"] = get_row_value(existing_user, "living_place", "")
                update_user_last_seen(existing_user["id"])
                load_recent_self_assessment(existing_user["id"])
                response = (
                    f"Welcome back, {name}. We talked last time. "
                    "Tell me, how have you been feeling lately?"
                )
                session["stage"] = STAGE_LISTEN
            else:
                new_user_id = create_user(name, account_id=session.get("account_id"))
                session["user_id"] = new_user_id
                session["is_returning_user"] = False
                session["role"] = "default"
                load_recent_self_assessment(new_user_id)
                response = (
                    f"Hello, {name}. It's nice to meet you. "
                    "Can you tell me a little about your daily life right now — "
                    "are you studying, working, caring for family, or managing something else?"
                )
                session["stage"] = STAGE_ROLE

        else:
            session["stage"] = STAGE_LISTEN
            response = "I am here with you. What has been weighing on your heart these days?"

    elif stage == STAGE_ROLE:
        role = detect_role(user_message)
        session["role"] = role

        if session.get("user_id"):
            update_user_role(session["user_id"], role)

        if session.get("sa_followup_needed"):
            response = (
                f"Thank you, {session.get('name', '')}. I also noticed you completed a self-assessment recently. "
                f"Before we continue, how have you been feeling lately?"
            )
        else:
            response = f"Thank you, {session.get('name', '')}. How have you been feeling lately?"

        session["stage"] = STAGE_LISTEN

    elif stage == STAGE_LISTEN:
        if wants_assessment(user_message):
            response = _check_and_start_assessment()
            add_to_history("assistant", response)
            if session.get("user_id"):
                save_chat_message(session["user_id"], "assistant", response)
            return jsonify({"response": response, "type": "normal", "stage": session.get("stage")})

        # Natural response engine: uses LLM-style contextual generation when API key is available,
        # with a warm local fallback if not. This prevents rigid rule-based replies.
        print("LANGUAGE DEBUG BEFORE GENERATION:", session.get("language"))
        print("SA LANGUAGE DEBUG:", session.get("sa_language"))
       
        response = generate_natural_chat_response(
            user_message=user_message,
            sess=session,
            emotion=emotion,
            wellbeing_info=wellbeing_info
        )

        # LLM safety net: model found risk the keyword lists missed
        if SAFETY_CHECK_TOKEN in (response or ""):
            return _llm_safety_flag_response(user_message)

        # Happy / everyday message -> remove any "despite everything" style hardship lines
        if _is_light_message(user_message):
            response = _strip_hardship_framing(response)

        session["stage"] = STAGE_LISTEN

        # ── Self-assessment follow-up questions (max 3, at turns 1 / 4 / 8) ──
        # (never glued onto a recipe / step-by-step answer)
        if _is_practical_request(user_message):
            pass
        elif session.get("sa_followup_needed") and session.get("sa_followup_count", 0) < 3:
            sa_listen_turns = session.get("sa_listen_turns", 0) + 1
            session["sa_listen_turns"] = sa_listen_turns
            trigger_turns = [1, 4, 8]
            if sa_listen_turns in trigger_turns:
                concern = session.get("sa_dominant_concern", "mild")
                
                chat_language = str(
                    session.get("language", session.get("sa_language", "en"))
                ).lower().strip()

                if chat_language in ("ur", "urdu"):
                    followup_list = SA_FOLLOWUP_QUESTIONS_UR.get(
                        concern, SA_FOLLOWUP_QUESTIONS_UR["mild"]
                    )
                else:
                    followup_list = SA_FOLLOWUP_QUESTIONS.get(
                        concern, SA_FOLLOWUP_QUESTIONS["mild"]
                    )

                idx = session.get("sa_followup_count", 0)
                if idx < len(followup_list):
                    response += "\n\n" + followup_list[idx]
                    session["sa_followup_count"] = idx + 1
                    if session["sa_followup_count"] >= 3:
                        session["sa_followup_needed"] = False
        else:
            session["sa_listen_turns"] = session.get("sa_listen_turns", 0) + 1

        # One-time gentle assessment nudge after 4+ exchanges, for emotional users not yet suggested
        user_turns = sum(1 for m in session.get("history", []) if m.get("role") == "user")
        if (
            not session.get("assessment_suggested")
            and user_turns >= 4
            and emotion in ["depression", "sadness", "grief", "anxiety", "stress", "fear", "ptsd"]
            # Never push the assessment on a happy / grateful / everyday message
            and not _is_light_message(user_message)
            and session.get("words_of_hope_mood") not in ("positive",)
        ):
            user_id_for_check = session.get("user_id")
            already_done_this_week = False
            if user_id_for_check:
                last = get_last_assessment_for_user(user_id_for_check)
                already_done_this_week = days_since_assessment(last) < 7

            if not already_done_this_week:
                response += (
                    "\n\nWhenever you feel ready, I can also walk you through a short weekly check-in — "
                    "it takes about 5 minutes and helps me understand how you have been feeling this week. "
                    "Just say 'start assessment' if you would like to try it, or visit the Self Assessment page."
                )
                session["assessment_suggested"] = True

    elif stage == STAGE_ASSESS:
        name = session.get("name", "")

        if not session.get("assessment_started", False):
            response = _check_and_start_assessment()
            add_to_history("assistant", response)
            if session.get("user_id"):
                save_chat_message(session["user_id"], "assistant", response)
            return jsonify({"response": response, "type": "normal", "stage": session.get("stage")})

        q_index = session.get("q_index", 0)
        number_match = re.search(r"\b([0-4])\b", user_message)

        if number_match is None:
            # Repeat current question with hint
            if q_index < len(SA_QUESTIONS):
                cur = SA_QUESTIONS[q_index]
                hint = "(0–4)" if cur["scale"] == "who" else "(0–3)"
                response = f"Please reply with a number {hint}.\n\n{cur['text']}"
            else:
                response = "Please reply with a number to continue."
        else:
            score = int(number_match.group(1))

            # Save answer for current question
            if q_index < len(SA_QUESTIONS):
                cur = SA_QUESTIONS[q_index]
                max_val = 4 if cur["scale"] == "who" else 3
                clamped = min(max(score, 0), max_val)

                if cur["scale"] == "phq":
                    phq = dict(session.get("phq", {}))
                    phq[cur["key"]] = clamped
                    session["phq"] = phq
                    if cur["key"] == "phq9" and clamped >= 2:
                        session["crisis_active"] = True
                        session["crisis_level"] = "crisis"
                        session["q_index"] = q_index + 1
                        response = build_crisis_response(user_message, name)
                        add_to_history("assistant", response)
                        if session.get("user_id"):
                            save_chat_message(session["user_id"], "assistant", response)
                        return jsonify({"response": response, "type": "crisis"})
                elif cur["scale"] == "gad":
                    gad = dict(session.get("gad", {}))
                    gad[cur["key"]] = clamped
                    session["gad"] = gad
                elif cur["scale"] == "who":
                    who = dict(session.get("who", {}))
                    who[cur["key"]] = clamped
                    session["who"] = who

                session["q_index"] = q_index + 1
                q_index += 1

            # All 21 questions answered?
            if q_index >= len(SA_QUESTIONS):
                phq_total = sum(session.get("phq", {}).values())
                gad_total = sum(session.get("gad", {}).values())
                who_total = sum(session.get("who", {}).values())
                phq_q9 = session.get("phq", {}).get("phq9", 0)

                phq_sev = sa_get_phq9_severity(phq_total)[0]
                gad_sev = sa_get_gad7_severity(gad_total)[0]
                
                session["severity"] = phq_sev
                

                prev_assess = None
                if session.get("user_id"):
                    prev_assess = get_last_assessment_for_user(session["user_id"])
                    if prev_assess and prev_assess["phq9_total"] is None:
                        prev_assess = None

                advice, risk_level = generate_assessment_advice(phq_total, gad_total, who_total, phq_q9, prev_assess)
                session["last_advice"] = advice
                session["last_assessment_scores"] = {"phq9": phq_total, "gad7": gad_total, "who5": who_total}

                save_assessment_json(session)
                if session.get("user_id"):
                    save_assessment_to_db(session["user_id"], session, advice=advice, prev=prev_assess)

                session["stage"] = STAGE_RESPOND

                comparison = ""
                if prev_assess and prev_assess["phq9_total"] is not None:
                    chg = phq_total - prev_assess["phq9_total"]
                    if chg >= 3:
                        comparison = f" Your depression score has risen {chg} points since last week."
                    elif chg <= -3:
                        comparison = f" Your depression score improved by {abs(chg)} points from last week."

                response = (
                    f"Thank you for sharing all of that, {name}. Here is a summary of your results:\n\n"
                    f"PHQ-9 (Depression): {score_to_10(phq_total, 27)}/10 — {phq_sev}\n"
                    f"GAD-7 (Anxiety): {score_to_10(gad_total, 21)}/10 — {gad_sev}\n"
                    f"WHO-5 (Wellbeing): {score_to_10(who_total, 25)}/10{comparison}\n\n"
                    f"Here is some personal guidance based on your scores:\n\n{advice}"
                )

                if risk_level == "crisis":
                    session["crisis_active"] = True
                    session["crisis_level"] = "crisis"
            else:
                # Ask next question
                nxt = SA_QUESTIONS[q_index]
                if nxt["scale"] == "who":
                    hint = "(0=At no time  1=Some of the time  2=Less than half  3=More than half  4=All of the time)"
                else:
                    hint = "(0=Not at all  1=Several days  2=More than half the days  3=Nearly every day)"
                response = nxt["text"] + "\n" + hint

    elif stage == STAGE_RESPOND:
        if wants_assessment(user_message):
            response = _check_and_start_assessment()
            add_to_history("assistant", response)
            if session.get("user_id"):
                save_chat_message(session["user_id"], "assistant", response)
            return jsonify({"response": response, "type": "normal", "stage": session.get("stage")})

        response = get_response(user_message, session, crisis_level, emotion)

        # LLM safety net: model found risk the keyword lists missed
        if SAFETY_CHECK_TOKEN in (response or ""):
            return _llm_safety_flag_response(user_message)

    else:
        response = "I am here with you. Can you tell me what has been feeling heaviest for you lately?"

    # Final strict topic guard: if the user did not mention sleep/rest/tiredness/energy,
    # do not let model-generated replies suddenly move to sleep.
    _user_l = user_message.lower()
    _sleep_terms = ["sleep", "slept", "sleeping", "insomnia", "night", "awake", "rest", "tired", "drained", "exhausted", "fatigue", "weak", "energy"]
    _response_l = (response or "").lower()
    if not any(t in _user_l for t in _sleep_terms) and any(t in _response_l for t in ["sleep", "sleeping", "slept", "insomnia"]):
        response = build_local_fallback_response(user_message, session, emotion)

    add_to_history("assistant", response)

    if session.get("user_id"):
        save_chat_message(session["user_id"], "assistant", response)

    return jsonify({
        "response": response,
        "type": "normal",
        "stage": session.get("stage"),
        "is_returning_user": session.get("is_returning_user", False)
    })

@app.route("/logs/<name>", methods=["GET"])
def view_logs(name):
    user = find_existing_user(name)
    if not user:
        return jsonify({"error": "User not found"}), 404

    logs = get_recent_wellbeing_logs(user["id"], limit=20)
    return jsonify({
        "user": {
            "id": user["id"],
            "name": user["name"],
            "role": user["role"],
            "first_seen": user["first_seen"],
            "last_seen": user["last_seen"]
        },
        "logs": [dict(row) for row in logs]
    })


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .assessment import (  # noqa: E402
    SA_FOLLOWUP_QUESTIONS,
    SA_FOLLOWUP_QUESTIONS_UR,
    SA_QUESTIONS,
    _check_and_start_assessment,
    generate_assessment_advice,
    load_recent_self_assessment,
    sa_get_gad7_severity,
    sa_get_phq9_severity,
    save_assessment_json,
    score_to_10,
)
from .context import (  # noqa: E402
    STAGE_ASSESS,
    STAGE_LISTEN,
    STAGE_NAME,
    STAGE_RESPOND,
    STAGE_ROLE,
    add_to_history,
    detect_idp_hardships,
    detect_role,
    init_session,
    save_wellbeing_if_mentioned,
)
from .core import (  # noqa: E402
    SAFETY_CHECK_TOKEN,
)
from .database import (  # noqa: E402
    create_user,
    days_since_assessment,
    detect_living_place,
    find_existing_user,
    get_db,
    get_last_assessment_for_user,
    get_last_chat_message,
    get_recent_wellbeing_logs,
    get_row_value,
    get_urdu_name_for_account,
    get_user_by_account_id,
    get_user_by_id,
    get_user_memory_profile,
    has_real_user_history,
    init_db,
    save_assessment_to_db,
    save_chat_message,
    update_user_last_seen,
    update_user_living_place,
    update_user_memory_profile,
    update_user_role,
    users_has_account_id,
)
from .language import (  # noqa: E402
    _is_light_message,
    _strip_hardship_framing,
)
from .responses import (  # noqa: E402
    build_local_fallback_response,
    generate_natural_chat_response,
    get_response,
    _is_practical_request,
)
from .safety import (  # noqa: E402
    build_crisis_response,
    build_grief_dream_support_response,
    build_psychosis_support_response,
    detect_emotion,
    direct_crisis_override,
    get_crisis_followup_response,
    get_crisis_support_message,
    run_safety_checks,
    get_hopelessness_check_response,
    is_hopeless_message,
)
from .text_utils import (  # noqa: E402
    is_positive_message,
    looks_like_name,
    normalize_for_safety,
    wants_assessment,
)
from .words_of_hope import (  # noqa: E402
    update_words_of_hope_mood,
)

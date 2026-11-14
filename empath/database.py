"""Database: users, chat history, memory profile, wellbeing logs, assessments, Urdu names.

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
)


# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(
            DB_PATH,
            timeout=30
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA busy_timeout = 30000")

    return g.db


@app.teardown_appcontext
def close_db(error=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER UNIQUE,
            name TEXT NOT NULL,
            role TEXT DEFAULT 'default',
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            living_place TEXT DEFAULT '',
            FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wellbeing_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            log_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            sleep_hours REAL,
            mood TEXT,
            energy TEXT,
            source_text TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            sender TEXT NOT NULL,
            message TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_memory (
            user_id INTEGER PRIMARY KEY,
            is_displaced INTEGER DEFAULT 0,
            living_place TEXT DEFAULT '',
            main_stressors TEXT DEFAULT '[]',
            sleep_pattern TEXT DEFAULT '',
            mood_pattern TEXT DEFAULT '',
            energy_pattern TEXT DEFAULT '',
            risk_pattern TEXT DEFAULT '',
            preferred_support_style TEXT DEFAULT 'gentle, natural, culturally aware support',
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            assessment_date DATETIME DEFAULT CURRENT_TIMESTAMP,
            phq9_total INTEGER,
            gad7_total INTEGER,
            who5_total INTEGER,
            severity TEXT,
            emotion_detected TEXT,
            displacement_cause TEXT,
            idp_hardships TEXT,
            advice_given TEXT,
            prev_phq9 INTEGER,
            prev_gad7 INTEGER,
            prev_who5 INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    cursor.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                dob TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

    # Migrate existing DB — add columns if missing
    for col_sql in [
        "ALTER TABLE users ADD COLUMN account_id INTEGER UNIQUE",
        "ALTER TABLE users ADD COLUMN living_place TEXT DEFAULT ''",
            "ALTER TABLE users ADD COLUMN gnuhealth_id TEXT",
        "ALTER TABLE assessments ADD COLUMN advice_given TEXT",
        "ALTER TABLE assessments ADD COLUMN prev_phq9 INTEGER",
        "ALTER TABLE assessments ADD COLUMN prev_gad7 INTEGER",
        "ALTER TABLE assessments ADD COLUMN prev_who5 INTEGER",
        "ALTER TABLE accounts ADD COLUMN urdu_name TEXT DEFAULT ''",
        "ALTER TABLE user_memory ADD COLUMN words_of_hope_mood TEXT DEFAULT ''",
    ]:
        try:
            cursor.execute(col_sql)
        except Exception:
            pass

   

    db.commit()

def generate_urdu_name(name):
    """
    Convert a person's Roman/English name into its natural Urdu spelling.
    """

    if not name:
        return ""

    try:
        prompt = f"""
Convert this person's name from Roman English into its natural and commonly used Urdu spelling.

This is TRANSLITERATION, NOT translation.

Important:
- This is a person's name.
- Write the name exactly as a Pakistani/Urdu speaker would normally write it in Urdu.
- Use natural Urdu spelling and correct Urdu phonetics.
- Do not translate the meaning of the name.
- Do not invent unusual spellings.
- Preserve first name and last name separately.
- Return ONLY the Urdu name.
- No explanation.
- No quotation marks.

Examples of correct natural Urdu spellings:
Amna -> آمنہ
Aamna -> آمنہ
Ayesha -> عائشہ
Fatima -> فاطمہ
Hina -> حنا
Usman -> عثمان
Saleha -> صالحہ
Asad -> اسد
Muhammad -> محمد

Name to transliterate:
{name}
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0
        )

        urdu_name = response.choices[0].message.content.strip()

        return urdu_name if urdu_name else name

    except Exception as e:
        print("Urdu name generation error:", e)
        return name
    
    def get_urdu_name_for_account(account_id):
        if not account_id:
            return ""

    db = get_db()

    row = db.execute(
        "SELECT full_name, urdu_name FROM accounts WHERE id = ?",
        (account_id,)
    ).fetchone()

    if not row:
        return ""

    # If Urdu name already exists, use it
    if row["urdu_name"]:
        return row["urdu_name"]

    # Generate it only the first time
    urdu_name = generate_urdu_name(row["full_name"])

    if urdu_name:
        db.execute(
            "UPDATE accounts SET urdu_name = ? WHERE id = ?",
            (urdu_name, account_id)
        )
        db.commit()

    return urdu_name

def hash_password(password):
    """Secure password hashing for new accounts."""
    return generate_password_hash(password)


def verify_password(password, stored_hash):
    """
    Verify new Werkzeug hashes and old SHA-256 hashes.
    This keeps existing users able to log in.
    """
    if not stored_hash:
        return False

    # New secure password hashes
    if stored_hash.startswith(("scrypt:", "pbkdf2:")):
        return check_password_hash(stored_hash, password)

    # Legacy SHA-256 password verification
    old_hash = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(old_hash, stored_hash)


def get_table_columns(table_name):
    db = get_db()
    cursor = db.cursor()
    try:
        cursor.execute(f"PRAGMA table_info({table_name})")
        return [row[1] for row in cursor.fetchall()]
    except Exception:
        return []

def users_has_account_id():
    return "account_id" in get_table_columns("users")

def normalize_name(name):
    return re.sub(r"\s+", " ", name.strip().lower())

def find_existing_user(name):
    db = get_db()
    cursor = db.cursor()
    norm_name = normalize_name(name)
    cursor.execute("""
        SELECT * FROM users
        WHERE lower(trim(name)) = ?
        ORDER BY id ASC
        LIMIT 1
    """, (norm_name,))
    return cursor.fetchone()


def create_user(name, account_id=None):
    db = get_db()
    cursor = db.cursor()
    clean_name = name.strip()

    if users_has_account_id():
        cursor.execute(
            """
            INSERT INTO users (name, account_id)
            VALUES (?, ?)
            """,
            (clean_name, account_id)
        )
    else:
        cursor.execute(
            """
            INSERT INTO users (name)
            VALUES (?)
            """,
            (clean_name,)
        )

    db.commit()
    return cursor.lastrowid


def get_user_by_account_id(account_id):
    if not account_id or not users_has_account_id():
        return None

    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE account_id = ? LIMIT 1", (account_id,))
    return cursor.fetchone()



def set_gnuhealth_id(user_id, gnuhealth_id):
    """Safely link a local Empath AI user to their MyGNUHealth profile ID."""
    if not user_id or not gnuhealth_id:
        return False

    db = get_db()
    cursor = db.cursor()

    cursor.execute(
        "SELECT gnuhealth_id FROM users WHERE id = ?",
        (user_id,)
    )
    row = cursor.fetchone()

    if not row:
        return False

    current_id = row["gnuhealth_id"]

    # Allow an empty link or the same existing link.
    if current_id and current_id != gnuhealth_id:
        app.logger.error(
            "Refusing to replace existing GNU Health ID for user %s",
            user_id
        )
        return False

    if current_id == gnuhealth_id:
        return True

    cursor.execute(
        """
        UPDATE users
        SET gnuhealth_id = ?
        WHERE id = ?
          AND (gnuhealth_id IS NULL OR gnuhealth_id = '')
        """,
        (gnuhealth_id, user_id)
    )
    db.commit()
    return cursor.rowcount == 1


def get_or_create_user_for_account(account_id, account_name):
    existing = get_user_by_account_id(account_id)
    if existing:
        return existing

    first_name = (account_name or "User").strip().split()[0].capitalize()
    new_user_id = create_user(first_name, account_id=account_id)
    return get_user_by_id(new_user_id)


def update_user_last_seen(user_id):
    db = get_db()

    try:
        db.execute("""
            UPDATE users
            SET last_seen = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (user_id,))

        db.commit()

    except sqlite3.OperationalError:
        db.rollback()
        raise


def update_user_role(user_id, role):
    db = get_db()
    db.execute("""
        UPDATE users
        SET role = ?, last_seen = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (role, user_id))
    db.commit()

def update_user_living_place(user_id, living_place):
    if not user_id or not living_place:
        return
    if "living_place" not in get_table_columns("users"):
        return
    db = get_db()
    db.execute("""
        UPDATE users
        SET living_place = ?, last_seen = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (living_place, user_id))
    db.commit()

def get_row_value(row, key, default=""):
    try:
        if row and key in row.keys():
            return row[key] if row[key] is not None else default
    except Exception:
        pass
    return default

def detect_living_place(text):
    t = normalize_for_safety(text)
    if any(x in t for x in ["ngo", "ngo camp", "ngo shelter"]):
        return "an NGO"
    if any(x in t for x in ["camp", "tent", "relief camp"]):
        return "a camp"
    if any(x in t for x in ["shelter", "temporary shelter"]):
        return "a shelter"
    if any(x in t for x in ["relatives", "relative", "cousin", "uncle", "aunt", "family home"]):
        return "with relatives"
    if any(x in t for x in ["friend", "friends"]):
        return "with friends"
    if any(x in t for x in ["rented", "rent", "rental"]):
        return "a rented place"
    if any(x in t for x in ["host family", "host"]):
        return "with a host family"
    return ""

def user_is_idp_context(sess):
    """Empath AI is designed for IDPs in Pakistan, so IDP context is default.

    We keep this as background context, not a phrase repeated to the user.
    Only turn it off if the user clearly says they are not displaced / not an IDP.
    """
    history = list(sess.get("history", []))
    full_text = " ".join([
        str(item.get("content", "")).lower()
        for item in history
        if item.get("role") == "user"
    ])

    not_idp_phrases = [
        "i am not displaced", "i'm not displaced", "i am not an idp", "i'm not an idp",
        "not an idp", "not displaced", "i was never displaced"
    ]
    if any(p in full_text for p in not_idp_phrases):
        return False

    return True

def get_user_by_id(user_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cursor.fetchone()

def save_chat_message(user_id, sender, message):
    db = get_db()
    db.execute("""
        INSERT INTO chat_history (user_id, sender, message)
        VALUES (?, ?, ?)
    """, (user_id, sender, message))
    db.commit()


def _memory_json_list(value):
    try:
        data = json.loads(value or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _memory_join_unique(old_items, new_items, limit=8):
    items = list(old_items or [])
    for item in new_items:
        if item and item not in items:
            items.append(item)
    return items[-limit:]

def ensure_user_memory(user_id):
    if not user_id:
        return None
    db = get_db()
    row = db.execute("SELECT * FROM user_memory WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        db.execute("INSERT INTO user_memory (user_id, main_stressors) VALUES (?, ?)", (user_id, json.dumps([])))
        db.commit()
        row = db.execute("SELECT * FROM user_memory WHERE user_id = ?", (user_id,)).fetchone()
    return row

def update_user_memory_profile(user_id, user_message, emotion="unknown", crisis_level="safe"):
    if not user_id or not user_message:
        return
    db = get_db()
    row = ensure_user_memory(user_id)
    if not row:
        return
    t = user_message.lower()
    is_displaced = int(row["is_displaced"] or 0)
    if any(k in t for k in ["displaced", "displacement", "idp", "left my home", "lost my home", "forced to leave", "camp", "shelter", "ngo", "temporary place"]):
        is_displaced = 1
    living_place = row["living_place"] or ""
    detected_place = detect_living_place(user_message)
    if detected_place:
        living_place = detected_place
        try:
            update_user_living_place(user_id, detected_place)
        except Exception:
            pass
    stressors = _memory_json_list(row["main_stressors"])
    found = []
    if any(k in t for k in ["study", "studies", "student", "exam", "class", "university", "focus"]): found.append("studies/focus")
    if detect_family_stress(t) or any(k in t for k in ["children pressure", "kids pressure", "parents pressure", "family responsibility", "too much responsibility"]): found.append("family responsibilities")
    if any(k in t for k in ["home", "miss my home", "lost my home", "left my home"]): found.append("loss of home")
    if any(k in t for k in ["camp", "shelter", "ngo", "crowded", "privacy", "noise"]): found.append("temporary living conditions")
    if any(k in t for k in ["job", "work", "income", "money", "fees", "expense"]): found.append("financial/work pressure")
    stressors = _memory_join_unique(stressors, found)
    extracted = extract_wellbeing_info_smart(user_message)
    sleep_pattern = row["sleep_pattern"] or ""
    mood_pattern = row["mood_pattern"] or ""
    energy_pattern = row["energy_pattern"] or ""
    if extracted.get("sleep_hours") is not None:
        h = extracted["sleep_hours"]
        sleep_pattern = f"very low sleep around {h:g} hours" if h <= 4 else f"short sleep around {h:g} hours" if h <= 6 else f"sleep around {h:g} hours"
    elif extracted.get("mentioned_sleep"):
        sleep_pattern = "sleep difficulty mentioned"
    if extracted.get("mood"):
        mood_pattern = extracted["mood"]
    elif extracted.get("mentioned_mood") or emotion in ["depression", "sadness", "grief", "anxiety", "fear", "stress", "ptsd"]:
        mood_pattern = emotion if emotion != "unknown" else (mood_pattern or "emotional distress")
    if extracted.get("energy"):
        energy_pattern = extracted["energy"]
    elif extracted.get("mentioned_energy"):
        energy_pattern = "low/changed energy mentioned"
    risk_pattern = row["risk_pattern"] or ""
    if crisis_level in ["crisis", "emergency"]:
        risk_pattern = crisis_level
    elif any(k in t for k in ["hopeless", "nothing matters", "meaningless", "empty"]):
        risk_pattern = "low hope / emotional emptiness"
    support_style = row["preferred_support_style"] or "gentle, natural, culturally aware support"
    if any(k in t for k in ["quran", "allah", "islam", "religion"]):
        support_style = "gentle support with Islamic/Quran reference when appropriate"
    db.execute("""
        UPDATE user_memory
        SET is_displaced=?, living_place=?, main_stressors=?, sleep_pattern=?, mood_pattern=?, energy_pattern=?, risk_pattern=?, preferred_support_style=?, last_updated=CURRENT_TIMESTAMP
        WHERE user_id=?
    """, (is_displaced, living_place, json.dumps(stressors), sleep_pattern, mood_pattern, energy_pattern, risk_pattern, support_style, user_id))
    db.commit()

def get_user_memory_profile(user_id):
    if not user_id:
        return ""
    row = ensure_user_memory(user_id)
    if not row:
        return ""
    parts = []
    if row["is_displaced"]:
        parts.append(f"User is displaced and staying in {row['living_place']}." if row["living_place"] else "User is an internally displaced person in Pakistan.")
    stressors = _memory_json_list(row["main_stressors"])
    if stressors: parts.append("Recurring stressors: " + ", ".join(stressors) + ".")
    if row["sleep_pattern"]: parts.append("Sleep pattern: " + row["sleep_pattern"] + ".")
    if row["mood_pattern"]: parts.append("Mood pattern: " + row["mood_pattern"] + ".")
    if row["energy_pattern"]: parts.append("Energy pattern: " + row["energy_pattern"] + ".")
    if row["risk_pattern"]: parts.append("Risk pattern: " + row["risk_pattern"] + ".")
    if row["preferred_support_style"]: parts.append("Preferred support: " + row["preferred_support_style"] + ".")
    return " ".join(parts)

def save_wellbeing_log(user_id, sleep_hours=None, mood=None, energy=None, source_text=""):
    db = get_db()
    db.execute("""
        INSERT INTO wellbeing_logs (user_id, sleep_hours, mood, energy, source_text)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, sleep_hours, mood, energy, source_text))
    db.commit()

def get_latest_wellbeing_log(user_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT * FROM wellbeing_logs
        WHERE user_id = ?
        ORDER BY log_date DESC, id DESC
        LIMIT 1
    """, (user_id,))
    return cursor.fetchone()

def get_recent_wellbeing_logs(user_id, limit=5):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT * FROM wellbeing_logs
        WHERE user_id = ?
        ORDER BY log_date DESC, id DESC
        LIMIT ?
    """, (user_id, limit))
    return cursor.fetchall()

def get_last_chat_message(user_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT id, sender, message, timestamp
        FROM chat_history
        WHERE user_id = ?
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """, (user_id,))
    return cursor.fetchone()


def get_latest_assessment(user_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT id, assessment_date
        FROM assessments
        WHERE user_id = ?
        ORDER BY assessment_date DESC, id DESC
        LIMIT 1
    """, (user_id,))
    return cursor.fetchone()


def has_real_user_history(user_row):
    if not user_row:
        return False

    role = (user_row["role"] or "default").strip().lower()
    if role != "default":
        return True

    if get_last_chat_message(user_row["id"]):
        return True

    if get_latest_assessment(user_row["id"]):
        return True

    return False

def get_last_assessment_for_user(user_id):
    db = get_db()
    cursor = db.cursor()
    cursor.execute("""
        SELECT * FROM assessments
        WHERE user_id = ?
        ORDER BY assessment_date DESC
        LIMIT 1
    """, (user_id,))
    return cursor.fetchone()

def days_since_assessment(row):
    if not row:
        return 999
    try:
        last_date = datetime.fromisoformat(str(row["assessment_date"]))
        return (datetime.now() - last_date).days
    except Exception:
        return 999

def save_assessment_to_db(user_id, session_data, advice="", prev=None):
    try:
        db = get_db()
        db.execute("""
            INSERT INTO assessments (
                user_id, phq9_total, gad7_total, who5_total,
                severity, emotion_detected, displacement_cause, idp_hardships,
                advice_given, prev_phq9, prev_gad7, prev_who5
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            sum(session_data.get("phq", {}).values()),
            sum(session_data.get("gad", {}).values()),
            sum(session_data.get("who", {}).values()),
            session_data.get("severity", "Unknown"),
            session_data.get("emotion", "Unknown"),
            session_data.get("displacement_cause", "Unknown"),
            json.dumps(session_data.get("idp_hardships", [])),
            advice,
            prev["phq9_total"] if prev else None,
            prev["gad7_total"] if prev else None,
            prev["who5_total"] if prev else None,
        ))
        db.commit()
    except Exception as e:
        print("Assessment DB save error:", e)

def generate_urdu_name(name):
    if not name:
        return ""

    try:
        prompt = f"""
Convert this person's name from Roman/English script into natural Urdu script.

Rules:
- This is a person's name.
- Transliterate the name, do not translate its meaning.
- Keep first and last names separate.
- Return ONLY the Urdu name.
- Do not add explanation or quotation marks.

Name: {name}
"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0
        )

        urdu_name = response.choices[0].message.content.strip()

        return urdu_name if urdu_name else name

    except Exception as e:
        print("Urdu name generation error:", e)
        return name


def get_urdu_name_for_account(account_id):
    if not account_id:
        return ""

    db = get_db()

    row = db.execute(
        "SELECT full_name, urdu_name FROM accounts WHERE id = ?",
        (account_id,)
    ).fetchone()

    if not row:
        return ""

    if row["urdu_name"]:
        return row["urdu_name"]

    urdu_name = generate_urdu_name(row["full_name"])

    if urdu_name:
        db.execute(
            "UPDATE accounts SET urdu_name = ? WHERE id = ?",
            (urdu_name, account_id)
        )
        db.commit()

    return urdu_name


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .context import (  # noqa: E402
    detect_family_stress,
    extract_wellbeing_info,
    extract_wellbeing_info_smart,
)
from .core import (  # noqa: E402
    DB_PATH,
    client,
)
from .text_utils import (  # noqa: E402
    normalize_for_safety,
)

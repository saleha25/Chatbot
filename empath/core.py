"""App setup: Flask app, OpenAI client, paths, helplines, ML models, login_required.

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


# The project folder (one level above this package): templates/, static/, data/ live there
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = Flask("app", root_path=PROJECT_DIR)
app.secret_key = "mental_health_fyp_2026"
app.permanent_session_lifetime = timedelta(days=30)

# Set your real API key in PowerShell before running:
# $env:OPENAI_API_KEY="your_real_api_key"
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ─────────────────────────────────────────────
# HELPLINES (verify before deploying)
# ─────────────────────────────────────────────
UMANG_NUMBER = "0311-7786264"
EMERGENCY_NUMBER = "1122"
ALLOWED_PHONE_NUMBERS = {UMANG_NUMBER, UMANG_NUMBER.replace("-", ""), EMERGENCY_NUMBER}

# How many calm messages in a row before crisis mode switches off
CRISIS_EXIT_SAFE_TURNS = 3

# The LLM replies with this exact token when it spots suicide / self-harm signs
# that the keyword lists missed (e.g. new Roman Urdu spellings).
SAFETY_CHECK_TOKEN = "[[SAFETY_CHECK]]"

FAITH_WORDS = [
    "allah", "quran", "dua", "namaz", "prayer", "pray", "islam", "iman", "sabr",
    "اللہ", "دعا", "نماز", "قرآن", "صبر",
]

SAFETY_CONFIRM_PHRASES = [
    "i am safe", "i'm safe", "im safe", "safe now", "yes safe",
    "not going to hurt myself", "won't hurt myself", "will not hurt myself",
    "i won't do anything", "i will not do anything",
    "family is with me", "my family is here", "family is there",
    "someone is with me", "my family is with me",
    "میں محفوظ ہوں", "گھر والے ساتھ ہیں", "میرے گھر والے یہاں ہیں",
    "ایسا کچھ نہیں کروں گا", "ایسا کچھ نہیں کروں گی",
    "خود کو نقصان نہیں پہنچاؤں",
    # Roman Urdu
    "main mehfooz hun", "mein mehfooz hoon", "main safe hun", "mein safe hoon",
    "aisa kuch nahi karunga", "aisa kuch nahi karungi",
    "khud ko nuqsan nahi", "ghar wale sath hain", "ghar walay saath hain",
]

BASE_PATH = PROJECT_DIR
DATA_PATH = os.path.join(BASE_PATH, "data")
DB_PATH = os.path.join(BASE_PATH, "empath_ai.db")
# def enable_sqlite_wal():
#     conn = sqlite3.connect(DB_PATH, timeout=30)
#     try:
#         conn.execute("PRAGMA journal_mode=WAL")
#         conn.execute("PRAGMA busy_timeout=30000")
#     finally:
#         conn.close()

# enable_sqlite_wal()

print("Loading models...")

with open(os.path.join(DATA_PATH, "crisis_detector.pkl"), "rb") as f:
    crisis_data = pickle.load(f)

with open(os.path.join(DATA_PATH, "emotion_detector_model.pkl"), "rb") as f:
    emotion_model = pickle.load(f)

with open(os.path.join(DATA_PATH, "tfidf_vectorizer.pkl"), "rb") as f:
    tfidf = pickle.load(f)
    

with open(os.path.join(DATA_PATH, "guardrail.pkl"), "rb") as f:
    guardrail_data = pickle.load(f)

RETRIEVAL_DATA = None
retrieval_path = os.path.join(DATA_PATH, "retrieval_system.pkl")
if os.path.exists(retrieval_path):
    with open(retrieval_path, "rb") as f:
        RETRIEVAL_DATA = pickle.load(f)
    print(f"Retrieval system loaded: {len(RETRIEVAL_DATA['qa_pairs'])} QA pairs")
else:
    print("WARNING: retrieval_system.pkl not found!")

CRISIS_PATTERNS = crisis_data["patterns"]
FINALITY_WORDS = crisis_data["finality_words"]
ACTION_WORDS = crisis_data["action_words"]
RELEASE_WORDS = crisis_data["release_words"]

BLOCKED_TOPICS = guardrail_data["blacklist_combos"]
FRAMING_PATTERNS = guardrail_data["framing_patterns"]
JAILBREAK_PATTERNS = guardrail_data["jailbreak_patterns"]
ESCALATION_PATTERNS = guardrail_data["escalation_patterns"]
SYNONYM_PATTERNS = guardrail_data["synonym_patterns"]
MANIPULATION_PATTERNS = guardrail_data["manipulation_patterns"]

print("All models loaded!")

# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("account_id"):
            return redirect(url_for("auth_page"))
        return f(*args, **kwargs)
    return decorated

"""Text helpers: cleaning, normalising, simple message checks.

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
    emotion_model,
    tfidf,
)



def is_metaphor_kill(text):
    """Return True when 'kill' is used as a harmless metaphor, not self-harm.

    Examples treated as safe: 'kill this exam', 'kill this project'.
    Examples NOT safe: 'kill myself', 'kill me', 'kill someone'.
    """
    t = normalize_for_safety(text or "")

    dangerous_targets = [
        "myself", "my self", "me", "yourself", "him", "her", "them",
        "someone", "person", "people", "friend", "mother", "father",
        "sister", "brother", "child", "wife", "husband"
    ]
    if any(f"kill {target}" in t for target in dangerous_targets):
        return False

    safe_patterns = [
        r"\bkill\s+(this|the|my)?\s*(exam|project|assignment|task|test|presentation|work|homework|quiz|paper)\b",
        r"\bkill\s+it\b",
        r"\bkill\s+(the\s+)?(game|competition)\b",
    ]

    return any(re.search(pattern, t) for pattern in safe_patterns)

def clean_text(text):
    text = str(text).lower()

    text = re.sub(r"http\S+", "", text)

    # Preserve English and Urdu characters
    text = re.sub(
        r"[^a-zA-Z\u0600-\u06FF\s]",
        "",
        text
    )

    return re.sub(r"\s+", " ", text).strip()

print("\n========== EMOTION MODEL TEST ==========")

print("Emotion classes:", emotion_model.classes_)

print("TF-IDF vocabulary size:", len(tfidf.vocabulary_))

print("English test:", clean_text("I am feeling very sad"))

print("Urdu test:", clean_text("میں بہت اداس ہوں"))

print("========================================\n")


def normalize_text(text):
    text = text.lower()
    text = re.sub(r"([a-z])-([a-z])", r"\1\2", text)
    text = text.replace("1", "i").replace("0", "o").replace("3", "e").replace("4", "a").replace("5", "s")
    return re.sub(r"(.)\1{2,}", r"\1", text)

def normalize_for_safety(text):
    t = text.lower().strip()
    replacements = {
        "sucide": "suicide",
        "suicde": "suicide",
        "kill my self": "kill myself",
        "end my self": "end myself",
        "dont want to live": "don't want to live",
        "wanna die": "want to die",
        "diee": "die",
        "myslef": "myself",
        "kll myself": "kill myself",
        "kil myself": "kill myself",
        "sui cide": "suicide",
        "freind": "friend",
        "frend": "friend",
        "cant": "can't",
        "wont": "won't",
        "dont": "don't",
    }
    for wrong, correct in replacements.items():
        t = t.replace(wrong, correct)
    return normalize_text(t)

def has_grief_intent(text):
    return any(re.search(p, text.lower()) for p in GRIEF_INTENT_PATTERNS)

def is_positive_message(text):
    t = clean_text(text)
    negative_patterns = [r"\bnot\s+good\b", r"\bnot\s+fine\b", r"\bnot\s+okay\b", r"\bi am sad\b", r"\bfeel bad\b", r"\bno point\b", r"\bworthless\b"]
    positive_patterns = [r"\bi am happy\b", r"\bi feel good\b", r"\bi am fine\b", r"\bi am okay\b", r"\bi feel better\b", r"\bi feel great\b"]

    for pattern in negative_patterns:
        if re.search(pattern, t):
            return False
    for pattern in positive_patterns:
        if re.search(pattern, t):
            return True
    return False

def wants_assessment(text):
    t = text.lower().strip()
    return any(trigger in t for trigger in [
        "start assessment", "start screening", "mental health assessment",
        "check my mental health", "screen me", "ask me questions",
        "start phq", "start gad", "assessment", "screening"
    ])

def looks_like_name(text):
    t = text.strip()
    if len(t.split()) > 3:
        return False

    lower_t = normalize_for_safety(t)
    bad_signals = [
        "kill", "die", "suicide", "sad", "depressed", "help", "anxious",
        "alone", "lost", "home", "stress", "cry", "hopeless", "hurt",
        "dream", "voices", "blood"
    ]
    if any(word in lower_t for word in bad_signals):
        return False

    return bool(re.fullmatch(r"[A-Za-z ]{2,30}", t))


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .context import (  # noqa: E402
    GRIEF_INTENT_PATTERNS,
)

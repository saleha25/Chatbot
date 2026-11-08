"""Reply quality: Urdu & English style rules, examples, auto-fixes, follow-up questions.

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



# ─────────────────────────────────────────────
# SHARED LANGUAGE HELPERS
# ─────────────────────────────────────────────
URDU_STYLE_RULES = """
URDU STYLE (only when the reply language is Urdu):
- Write natural, everyday Pakistani Urdu, the way a kind counsellor in Pakistan would speak.
  Never translate English sentences word for word.
  Avoid stiff translated phrases such as "آپ کے احساسات سمجھنے کے قابل ہیں",
  "آپ کی یہ بات سمجھنا ضروری ہے", "آپ نے اس کی حمایت کی ہے".
  Prefer natural ones such as "آپ کا ایسا محسوس کرنا بالکل سمجھ میں آتا ہے", "یہ سن کر دل خوش ہو گیا".
- When you refer to yourself, ALWAYS use feminine forms: "میں سمجھتی ہوں", "میں سن رہی ہوں", "میں چاہتی ہوں".
- For the user, follow the gender shown in the user's own words ("کروں گا" = male, "کروں گی" = female).
  If it is not clear, use respectful neutral forms with "آپ" (for example "آپ کیسا محسوس کر رہے ہیں").
  Never assume the user is female or male without a clue.
- Check grammar carefully: gender and number agreement ("اس کے پہلے دن کے تجربات", not "اس کی پہلی دن کی تجربات").
- If the user uses Islamic expressions such as "الحمدللہ" or "ان شاء اللہ", you may answer naturally
  in the same way (for example "ماشاءاللہ").
- Do not end replies with the same closing line every time (for example "آپ کی بات سننا میرے لیے اہم ہے",
  "میں یہاں ہوں"). Use a closing line only sometimes.
""".strip()

URDU_SCRIPT_RE = r"[؀-ۿݐ-ݿࢠ-ࣿ]"


# Small models learn tone far better from examples than from rules.
URDU_EXAMPLES = """
GOOD URDU REPLY EXAMPLES (copy the tone and quality, never copy the words):

User: سب ہمیں نظرانداز کرتے ہیں، مجھے بہت غصہ آتا ہے۔
Reply:
جب بار بار ایسا لگے کہ کوئی آپ کی بات سن ہی نہیں رہا، تو غصہ آنا بالکل سمجھ میں آتا ہے۔ یہ غصہ بتاتا ہے کہ آپ کو کتنی تکلیف پہنچی ہے۔

جب غصہ بہت بڑھ جائے تو تھوڑی دیر تیز قدموں سے چل لیں، یا کسی کپڑے کو زور سے مٹھی میں دبائیں۔ اس سے جسم کا تناؤ کچھ کم ہوتا ہے۔

آج کس بات نے سب سے زیادہ دکھ دیا؟

User: آج کل بہت اکیلا محسوس کرتا ہوں، گھر بہت یاد آتا ہے۔
Reply:
اپنا گھر یاد آنا اور ساتھ میں اکیلے پن کا احساس، دونوں مل کر دل کو بہت بھاری کر دیتے ہیں۔

گھر صرف دیواریں نہیں ہوتا، وہاں کی عادتیں، لوگ اور سکون بھی ہوتا ہے، اس لیے اس کی کمی محسوس ہونا فطری ہے۔

کیا آس پاس کوئی ایسا ہے جس کے ساتھ آپ تھوڑی دیر بیٹھ کر بات کر سکیں؟

User: الحمدللہ! آج میرے بیٹے کا سکول میں پہلا دن تھا اور وہ بہت خوش تھا۔
Reply:
ماشاءاللہ! بچے کا پہلا دن اور وہ بھی خوشی خوشی، یہ تو بہت پیارا لمحہ ہے۔

اس نے واپس آ کر سب سے پہلے کیا بتایا؟

User: بس بہت تھک گیا ہوں۔
Reply:
آپ کی تھکن آپ کی باتوں سے صاف محسوس ہو رہی ہے۔ جب دل اور جسم دونوں تھکے ہوں تو ہر کام بھاری لگتا ہے۔

آج رات بس اتنا کر لیں: سونے سے پہلے چار تک گن کر سانس اندر لیں، چار تک روکیں، اور چھ تک گن کر آہستہ باہر نکالیں۔ تین چار بار کافی ہے۔

User: کسی سے بات کرنے کا دل نہیں کر رہا۔
Reply:
کوئی بات نہیں، ابھی کچھ کہنا ضروری نہیں۔ کبھی کبھی بس خاموش بیٹھنا بھی ٹھیک ہوتا ہے۔

جب دل کرے، ایک لفظ بھی لکھ دیں، میں سن رہی ہوں۔
""".strip()


# Known mistakes the small model keeps making in Urdu -> fixed by code.
URDU_WORD_FIXES = {
    "پاورفل": "شدید",
    "اپنی جذبات": "اپنے جذبات",
    "آپ کی جذبات": "آپ کے جذبات",
    "آپ کے احساسات سمجھنے کے قابل ہیں": "آپ کا ایسا محسوس کرنا سمجھ میں آتا ہے",
    "آپ کی یہ بات سمجھنا ضروری ہے کہ ": "",
    "آپ نے اس کی حمایت کی ہے": "آپ نے اس کا ساتھ دیا ہے",
}

# The bot speaks as a woman in Urdu: first-person masculine verbs -> feminine.
# ("…تا ہوں" / "رہا ہوں" / "چکا ہوں" are only ever first-person singular.)
URDU_SELF_GENDER_FIXES = [
    (r"تا ہوں", "تی ہوں"),
    (r"رہا ہوں", "رہی ہوں"),
    (r"چکا ہوں", "چکی ہوں"),
]

# "Have you ever tried…?" questions put pressure on the user
URDU_HAVE_YOU_TRIED_RE = r"کیا آپ نے کبھی[^؟?]*(کوشش|سوچا)[^؟?]*[؟?]"

# Closing lines that get repeated reply after reply
URDU_REPEATED_CLOSINGS = [
    "میں یہاں ہوں",
    "میں یہاں آپ کی بات سننے کے لیے ہوں",
    "آپ کی بات سننا میرے لیے اہم ہے",
    "آپ کی بات کرنا میرے لیے",
    "آپ کی مدد کے لیے یہاں ہوں",
]


def _polish_urdu_reply(text, previous_reply=""):
    """Fix the Urdu mistakes the small model keeps repeating, without another API call."""
    text = (text or "").strip()
    if not text:
        return text
    original = text

    for wrong, right in URDU_WORD_FIXES.items():
        text = text.replace(wrong, right)

    for pattern, replacement in URDU_SELF_GENDER_FIXES:
        text = re.sub(pattern, replacement, text)

    # Remove "have you ever tried…?" questions
    text = re.sub(URDU_HAVE_YOU_TRIED_RE, "", text)

    # Drop a closing line if the previous reply also had one
    previous_had_closing = any(c in (previous_reply or "") for c in URDU_REPEATED_CLOSINGS)
    if previous_had_closing:
        kept_paragraphs = []
        for paragraph in text.split("\n"):
            sentences = re.split(r"(?<=[۔؟!.?])\s+", paragraph)
            sentences = [s for s in sentences if not any(c in s for c in URDU_REPEATED_CLOSINGS)]
            kept_paragraphs.append(" ".join(sentences))
        text = "\n".join(kept_paragraphs)

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or original


# ─────────────────────────────────────────────
# ENGLISH QUALITY: examples + code-level clean-up (no extra API call)
# ─────────────────────────────────────────────
ENGLISH_EXAMPLES = """
GOOD ENGLISH REPLY EXAMPLES (copy the tone, length and quality, never copy the words):

User: i am sad today
Reply:
I'm sorry today feels heavy. You don't need a reason to feel sad, and you don't have to explain it all at once.

What's been sitting with you the most today?

User: I can't focus at all. The camp is so noisy and I feel lonely, all my friends are far away. I only sleep 3 hours.
Reply:
Noise all around, friends far away, and only three hours of sleep: no wonder focusing feels impossible. Anyone would struggle with that much at once.

Tonight, try one small thing: when you lie down, breathe in for 4, hold for 4, and breathe out slowly for 6, a few times. Earplugs or a cloth over your ears can also soften the noise.

Is there anyone in the camp, even one person, you could sit and talk with?

User: Everyone ignores us. I'm so angry I want to break something.
Reply:
Being ignored again and again is painful, and anger is a very human response to it.

When it builds up, walk fast for a few minutes or squeeze a cloth as hard as you can. It lets the body release some of that pressure safely.

What happened today that made it feel this strong?

User: I passed my exam with the highest marks!
Reply:
Congratulations, that's a big achievement! Top of the class takes real effort.

Who was the first person you told?

User: I'm feeling left out because my friends have been making plans without me, and lately I've started wondering whether I really matter to them.
Reply:
Watching plans happen without you hurts, and it hurts more when it starts to make you wonder whether you matter to them. That question can stay with you long after the plans are over.

When did you first notice things changing with them?

User: I'm surrounded by people every day, but sometimes I still feel completely alone. I don't think anyone really understands what goes on inside my mind.
Reply:
Being around people all day while no one sees what is happening inside you is a very lonely feeling, maybe lonelier than being on your own.

You don't have to explain it perfectly. If you'd like, share a little of what goes on in your mind here, and we can look at it together.

User: I'm scared that if I fail academically, I'll disappoint my parents and make them regret believing in me.
Reply:
That's a heavy mix of love and pressure: their belief in you clearly means a lot, and that is exactly why the fear of letting them down feels so big.

What part of your studies feels most uncertain right now?
""".strip()

# Openings the model keeps using even when told not to -> rewritten by code
ENGLISH_OPENING_FIXES = [
    (r"^It sounds like (you'?re|you are|you have|you'?ve)\b", r"\1"),
    (r"^It sounds (really|very|so|incredibly|quite)\s+", r"It's \1 "),
    (r"^That sounds (really|very|so|incredibly|quite)\s+", r"That's \1 "),
    (r"^It must be (really|very|so|incredibly|quite)\s+", r"It's \1 "),
    (r"^I can only imagine how (\w+) (it has been|it's been|it is|it must be|that must be|that is) ", r"It's really \1 "),
    (r"^I understand how (\w+) it (can be|is|must be) ", r"It \2 so \1 "),
]

# Filler sentences that add nothing -> removed when they stand alone
ENGLISH_FILLER_SENTENCES = [
    r"^I can only imagine how (that|this|it) (feels|must feel)( for you)?\.?$",
    r"^Your feelings are (important|valid)\.?$",
    r"^(It'?s|It is) completely (valid|okay|normal) to feel this way\.?$",
    r"^I'?m here (for you|to listen|with you)\.?$",
    r"^I'?m here to listen( to you)?( whenever you'?re ready)?\.?$",
]

ENGLISH_HAVE_YOU_TRIED_RE = r"[^.!?\n]*\b(Have|Did) you (ever )?(tried|thought about|considered)\b[^?]*\?"


ENGLISH_EMPTY_QUESTIONS_RE = (
    r"\s*(What do you think|How does that sound|Does that make sense|"
    r"What are your thoughts( on (that|this))?|Would you like to share more)\?\s*$"
)

# Suggestions to talk / open up / reach out to someone
TALK_SUGGESTION_RE = (
    r"\b(talk(ing)?|speak(ing)?|open(ing)? up|reach(ing)? out|shar(e|ing) (your|these|those) "
    r"(feelings|thoughts|worries))\b[^.!?]*\b(someone|them|friends?|parents|family|people|"
    r"counsellor|counselor|someone you trust)\b"
    r"|\bopen(ing)? up\b|\breach(ing)? out\b"
)


def _recent_assistant_messages(n=2):
    """The last n bot replies (the reply being written now is not in history yet)."""
    msgs = [str(m.get("content", "")) for m in session.get("history", []) if m.get("role") == "assistant"]
    return msgs[-n:]


def _polish_english_reply(text, previous_reply="", talk_guard=True):
    """Fix the English habits the small model keeps repeating, without another API call."""
    text = (text or "").strip()
    if not text:
        return text
    original = text

    # 1. Rewrite banned openings at the start of each paragraph
    fixed_paragraphs = []
    for paragraph in text.split("\n"):
        p = paragraph.strip()
        for pattern, replacement in ENGLISH_OPENING_FIXES:
            new_p = re.sub(pattern, replacement, p, count=1, flags=re.IGNORECASE)
            if new_p != p:
                p = new_p[:1].upper() + new_p[1:]
                break
        fixed_paragraphs.append(p)
    text = "\n".join(fixed_paragraphs)

    # 2. Turn "Have you tried / thought about…?" questions into gentle suggestions,
    #    so the useful idea stays but the pressure goes.
    def _to_suggestion(match):
        verb = match.group(3).lower()
        rest = match.group(4).strip()
        lead = {"tried": "You could try", "thought about": "You might think about",
                "considered": "You could consider", "had a chance to": "You could",
                "talked to": "You could talk to", "spoken to": "You could speak to",
                "shared": "You could share"}.get(verb, "You could try")
        return f"{lead} {rest}."
    text = re.sub(
        r"\b(Have|Did) you (ever )?(tried|thought about|considered|had a chance to|talked to|spoken to|shared)\s+([^?]*)\?",
        _to_suggestion, text, flags=re.IGNORECASE
    )

    # 2b. Remove empty follow-up questions
    text = re.sub(ENGLISH_EMPTY_QUESTIONS_RE, "", text, flags=re.IGNORECASE)

    # 2c. "Talk to someone" at most once in three replies
    #     (never in crisis mode, where reaching out to people matters most)
    recent = _recent_assistant_messages(2) if talk_guard else []
    if any(re.search(TALK_SUGGESTION_RE, r, flags=re.IGNORECASE) for r in recent):
        paragraphs = []
        for paragraph in text.split("\n"):
            sentences = re.split(r"(?<=[.!?])\s+", paragraph.strip())
            sentences = [x for x in sentences if not re.search(TALK_SUGGESTION_RE, x, flags=re.IGNORECASE)]
            paragraphs.append(" ".join(sentences))
        candidate = "\n".join(paragraphs).strip()
        if len(re.findall(r"[.!?]", candidate)) >= 2:   # keep only if the reply still has substance
            text = candidate

    # 3. Remove empty filler sentences
    kept_paragraphs = []
    for paragraph in text.split("\n"):
        sentences = re.split(r"(?<=[.!?])\s+", paragraph.strip())
        sentences = [
            s for s in sentences
            if not any(re.match(f, s.strip(), flags=re.IGNORECASE) for f in ENGLISH_FILLER_SENTENCES)
        ]
        kept_paragraphs.append(" ".join(sentences))
    text = "\n".join(kept_paragraphs)

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or original


def _last_assistant_message():
    for item in reversed(list(session.get("history", []))):
        if item.get("role") == "assistant":
            return str(item.get("content", ""))
    return ""


def _reply_language_ok(text, language):
    """Urdu mode: reply must be (almost) all Urdu script. English mode: no Urdu script at all."""
    text = text or ""
    urdu_letters = len(re.findall(URDU_SCRIPT_RE, text))
    latin_letters = len(re.findall(r"[A-Za-z]", text))

    if language == "ur":
        total = urdu_letters + latin_letters
        if total == 0:
            return True
        # A few Latin letters are allowed (e.g. "PHQ-9", "Empath AI").
        return urdu_letters / total >= 0.85

    return urdu_letters == 0


def _umang_reminder(language):
    """Safety line added by code, so it never depends on the LLM remembering it."""
    if language == "ur":
        return (
            f"اگر بھاری خیالات واپس آئیں تو اُمنگ {UMANG_NUMBER} پر مفت اور 24 گھنٹے بات کی جا سکتی ہے، "
            f"اور فوری خطرے میں {EMERGENCY_NUMBER} پر کال کریں۔"
        )
    return (
        f"If the heavy thoughts come back, Umang ({UMANG_NUMBER}) is free and available 24/7, "
        f"and {EMERGENCY_NUMBER} is there in an emergency."
    )


def _remove_extra_questions(text):
    """Keep max one natural follow-up question.

    Removes earlier question SENTENCES and keeps the last one, instead of turning
    extra '?' into '.', which produced broken questions like "Do you like it."
    Paragraph breaks are preserved.
    """
    text = (text or "").strip()
    if text.count("?") + text.count("؟") <= 1:
        return text

    paragraphs = text.split("\n")
    items = []  # [paragraph_index, sentence]
    for p_index, paragraph in enumerate(paragraphs):
        if not paragraph.strip():
            items.append([p_index, ""])
            continue
        for sentence in re.split(r"(?<=[.!?؟۔])\s+", paragraph):
            items.append([p_index, sentence])

    question_indexes = [
        i for i, (_, s) in enumerate(items)
        if s.strip().endswith(("?", "؟"))
    ]
    if len(question_indexes) <= 1:
        return text

    drop = set(question_indexes[:-1])
    rebuilt = []
    for p_index in range(len(paragraphs)):
        parts = [
            s for i, (pi, s) in enumerate(items)
            if pi == p_index and i not in drop and s.strip()
        ]
        rebuilt.append(" ".join(parts))

    result = "\n".join(rebuilt)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()
    return result or text


# ─────────────────────────────────────────────
# KEEP HAPPY / EVERYDAY REPLIES FREE OF HARDSHIP FRAMING
# ─────────────────────────────────────────────
HARDSHIP_FRAMING_PATTERNS = [
    r"despite (everything|all)",
    r"after (everything|all) (you'?ve|you have|you'?re|you are)",
    r"even (with|in|during) (so much change|everything|difficult|these difficult|your current|hard times|tough times)",
    r"in your current situation",
    r"\bdisplace(d|ment)\b",
    r"\bfloods?\b",
    r"\bcamps?\b",
    r"\bshelters?\b",
    r"\bhardships?\b",
    r"difficult (times|circumstances|situation)",
    r"everything (going on|you'?ve been through|you have been through)",
    r"اتنی تبدیلیوں",
    r"مشکل حالات",
    r"مشکل وقت",
    r"بے گھر",
    r"سیلاب",
]

LIGHT_MESSAGE_WORDS = [
    "thank", "thanks", "shukriya", "shukria", "jazakallah", "congrat",
    "passed", "won", "got the highest", "good news", "birthday",
    "شکریہ", "جزاک اللہ", "خوش", "مبارک",
]

DISTRESS_WORDS = [
    "sad", "stress", "worried", "anxious", "scared", "afraid", "hopeless",
    "cry", "angry", "alone", "lonely", "pain", "hurt", "tired", "exhausted",
    "flood", "camp", "lost my", "miss my", "can't sleep", "cannot sleep",
    "اداس", "پریشان", "ڈر", "اکیلا", "اکیلی", "غصہ", "تھکن", "سیلاب", "کیمپ",
    # Roman Urdu
    "udaas", "udas", "pareshan", "preshan", "dukhi", "ghussa", "gussa",
    "akela", "akeli", "thak gaya", "thak gayi", "dar lagta", "bura din", "bura guzra",
]


def _is_light_message(user_message):
    """True for happy, grateful or everyday messages with no distress in them."""
    t = normalize_for_safety(user_message or "")

    if any(w in t for w in DISTRESS_WORDS):
        return False

    explicit = _explicit_words_of_hope_mood(user_message)
    if explicit in ("sad", "anxious", "angry", "lonely", "tired", "homesick"):
        return False

    return bool(
        is_happy_message(user_message)
        or is_positive_message(user_message)
        or is_food_or_recipe_message(user_message)
        or explicit in ("positive", "hope")
        or any(w in t for w in LIGHT_MESSAGE_WORDS)
    )


def _strip_hardship_framing(text):
    """Remove sentences that drag hardship/displacement into a happy reply."""
    text = (text or "").strip()
    if not text:
        return text

    cleaned_paragraphs = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            cleaned_paragraphs.append("")
            continue
        sentences = re.split(r"(?<=[.!?؟۔])\s+", paragraph)
        kept = [
            s for s in sentences
            if not any(re.search(p, s, flags=re.IGNORECASE) for p in HARDSHIP_FRAMING_PATTERNS)
        ]
        cleaned_paragraphs.append(" ".join(kept))

    result = "\n".join(cleaned_paragraphs)
    result = re.sub(r"\n{3,}", "\n\n", result).strip()

    # Never return an empty reply; keep the original if everything matched.
    return result or text


def _avoid_repeated_opening(text):
    """Reduce repeated phrases that made the bot feel template-based."""
    repeated = [
        "I am really glad to hear that.",
        "Your positive thinking is meaningful",
        "I hope this happiness stays with you",
        "That is good to hear.",
    ]
    alternatives = [
        "That sounds like a genuinely lighter moment.",
        "There is something very gentle in what you just shared.",
        "That feels like a hopeful shift.",
        "I can hear a little ease in your words.",
    ]
    for phrase in repeated:
        if phrase.lower() in text.lower():
            text = re.sub(re.escape(phrase), random.choice(alternatives), text, flags=re.IGNORECASE)
    return text

def _add_hopeful_idp_line(text, user_message="", sess=None):
    """Insert exactly one hopeful IDP-sensitive line before the final follow-up question."""
    text = (text or "").strip()

    if not text or _has_idp_sensitive_line(text):
        return text

    line = _hopeful_idp_line(user_message, sess)

    # Detect selected language
    language = sess.get("language", "en") if sess else session.get("language", "en")

    # ============================================================
    # URDU MODE
    # ============================================================
    if language == "ur":

        # Urdu question mark
        qpos = text.rfind("؟")

        if qpos != -1:
            before = text[:qpos + 1]

            # Find the beginning of the final question
            split_at = max(
                before.rfind("\n", 0, qpos),
                before.rfind("۔ ", 0, qpos),
                before.rfind("! ", 0, qpos)
            )

            if split_at != -1:

                if before[split_at:split_at + 2] in ["۔ ", "! "]:
                    question = before[split_at + 2:].strip()
                    main = text[:split_at + 1].strip()

                else:
                    question = before[split_at:].strip()
                    main = text[:split_at].strip()

                if question.endswith("؟"):
                    return (
                        main
                        + "\n\n"
                        + line
                        + "\n\n"
                        + question
                    ).strip()

            # If we cannot split properly, put hopeful line before the question
            main = text[:qpos].strip()
            question = text[qpos:].strip()

            if question.endswith("؟"):
                return (
                    main
                    + "\n\n"
                    + line
                    + "\n\n"
                    + question
                ).strip()

        # If no Urdu question exists, add the line normally
        return (text.rstrip() + "\n\n" + line).strip()

    # ============================================================
    # ENGLISH MODE — ORIGINAL BEHAVIOR
    # ============================================================
    qpos = text.rfind("?")

    if qpos != -1:
        before = text[:qpos + 1]

        split_at = max(
            before.rfind("\n", 0, qpos),
            before.rfind(". ", 0, qpos),
            before.rfind("! ", 0, qpos)
        )

        if split_at != -1:

            if before[split_at:split_at + 2] in [". ", "! "]:
                question = before[split_at + 2:].strip()
                main = text[:split_at + 1].strip()

            else:
                question = before[split_at:].strip()
                main = text[:split_at].strip()

            if question.endswith("?"):
                return (
                    main
                    + "\n\n"
                    + line
                    + "\n\n"
                    + question
                ).strip()

    return (text.rstrip() + "\n\n" + line).strip()


def _has_idp_sensitive_line(text):
    """Avoid adding duplicate IDP-context lines if the response already has one."""
    t = (text or "").lower()
    markers = [
        "after everything", "with so much change", "when life has", "when life feels",
        "life has been", "unsettled", "difficult circumstances", "stability has been shaken",
        "everything you have been managing", "everything you’ve been managing"
    ]
    return any(m in t for m in markers)

def _hopeful_idp_line(user_message="", sess=None):
    """Return one soft IDP-sensitive hopeful line in the selected language."""

    t = normalize_for_safety(user_message or "")
    language = sess.get("language", "en") if sess else session.get("language", "en")

    # URDU MODE
    if language == "ur":
        if any(w in t for w in ["sleep", "tired", "exhausted", "drained", "energy", "weak"]):
            options = [
                "آپ نے جو کچھ برداشت کیا ہے، اس کے بعد تھوڑا سا آرام اور اپنا خیال رکھنا آپ کی طاقت بحال کرنے میں مدد دے سکتا ہے۔",
                "اتنی تبدیلیوں کے درمیان کم توانائی محسوس ہونا فطری ہے، اور آپ آہستہ آہستہ بہتر محسوس کر سکتی ہیں۔",
                "یہ تھکن کمزوری نہیں ہے؛ شاید آپ کا جسم مشکل وقت کے بعد آرام اور توجہ مانگ رہا ہے۔"
            ]

        elif any(w in t for w in ["job", "work", "money", "income", "unemployed", "earn", "fees", "expense"]):
            options = [
                "غیر یقینی حالات میں بھی ایک چھوٹا عملی قدم امید واپس لا سکتا ہے۔",
                "یہ دباؤ حقیقی ہے، لیکن یہ آپ کی قدر کا تعین نہیں کرتا۔",
                "اتنی ذمہ داریوں کے درمیان دباؤ محسوس ہونا فطری ہے، اور آپ مدد اور سہارا حاصل کرنے کی مستحق ہیں۔"
            ]

        elif any(w in t for w in ["sad", "low", "worse", "worst", "hopeless", "empty", "cry", "stress", "worried", "anxious", "fear", "panic"]):
            options = [
                "آپ کو یہ سب کچھ اکیلے برداشت کرنے کی ضرورت نہیں، اور آہستہ آہستہ حالات کو سنبھالا جا سکتا ہے۔",
                "مشکل حالات میں خود کو پریشان محسوس کرنا ناکامی نہیں؛ آپ کے جذبات توجہ اور نرمی کے مستحق ہیں۔",
                "چھوٹے اور مستقل قدم مشکل وقت میں بھی کچھ سکون اور امید پیدا کر سکتے ہیں۔"
            ]

        else:
            options = [
                "اتنی تبدیلیوں کے باوجود، آپ کے احساسات توجہ اور خیال کے مستحق ہیں۔",
                "آپ نے جو کچھ سنبھالا ہے، اس کے بعد اس لمحے کو نرمی سے لینا بھی مددگار ثابت ہو سکتا ہے۔",
                "مشکل حالات میں دل کی بات کرنا کبھی کبھی حالات کو تھوڑا زیادہ واضح کرنے میں مدد دے سکتا ہے۔"
            ]

        return random.choice(options)

    # ENGLISH MODE
    if any(w in t for w in ["sleep", "tired", "exhausted", "drained", "energy", "weak"]):
        options = [
            "After everything you have been carrying, giving yourself some rest can help you regain your strength.",
            "Feeling low on energy during times of change is understandable, and you can take things one step at a time.",
            "This tiredness is not a weakness; your mind and body may simply need some care and rest."
        ]

    elif any(w in t for w in ["job", "work", "money", "income", "unemployed", "earn", "fees", "expense"]):
        options = [
            "Even in uncertain circumstances, one small practical step can help bring back a sense of hope.",
            "This pressure is real, but it does not define your worth.",
            "With so many responsibilities, feeling pressured is understandable, and you also deserve support."
        ]

    elif any(w in t for w in ["sad", "low", "worse", "worst", "hopeless", "empty", "cry", "stress", "worried", "anxious", "fear", "panic"]):
        options = [
            "You do not have to carry everything alone, and things can be managed one step at a time.",
            "Feeling overwhelmed in difficult circumstances is not a failure; your feelings deserve care and attention.",
            "Small and steady steps can still create some relief and hope during difficult times."
        ]

    else:
        options = [
            "Even with so much change around you, your feelings still deserve space and care.",
            "After everything you have been managing, taking this one moment gently can still help.",
            "When life feels unsettled, being heard clearly can become one small source of steadiness."
        ]

    return random.choice(options)

def _add_hopeful_idp_line(text, user_message="", sess=None):
    """Insert exactly one hopeful IDP-sensitive line before the final follow-up question."""
    text = (text or "").strip()
    if not text or _has_idp_sensitive_line(text):
        return text

    line = _hopeful_idp_line(user_message, sess)

    # Put the IDP-sensitive line before the final question, so the response still ends with one question.
    question_mark = "؟" if sess and sess.get("language", "en") == "ur" else "?"
    qpos = text.rfind(question_mark)
    if qpos != -1:
        before = text[:qpos + 1]
        split_at = max(before.rfind("\n", 0, qpos), before.rfind(". ", 0, qpos), before.rfind("! ", 0, qpos))
        if split_at != -1:
            if before[split_at:split_at+2] in [". ", "! "]:
                question = before[split_at+2:].strip()
                main = text[:split_at+1].strip()
            else:
                question = before[split_at:].strip()
                main = text[:split_at].strip()
            if question.endswith(question_mark):
                return (main + "\n\n" + line + "\n\n" + question).strip()

    return (text.rstrip() + "\n\n" + line).strip()


def _ensure_one_followup(text, user_message, sess=None):
    """Ensure the response ends with exactly one relevant, natural follow-up question."""

    text = _remove_extra_questions(_avoid_repeated_opening(text))

       # Detect selected application language
    language = sess.get("language", "en") if sess else session.get("language", "en")

    # Do not force a follow-up question when the user only wants to be heard.
    user_text = normalize_for_safety(user_message or "")

    listening_phrases = [
        "just listen",
        "just want you to listen",
        "i only want you to listen",
        "i don't want advice",
        "i do not want advice",
        "no advice right now",
        "i don't need advice",
        "i do not need advice",
        "i just need someone to listen",
        "i just want someone to listen",
        "i need you to listen",
        "please just hear me out",
        "just hear me",
        "i want to vent",
        "let me vent",
        "i just need to talk",
        "i only need someone to hear me",
        "بس میری بات سنیں",
        "صرف میری بات سنیں",
        "مجھے مشورہ نہیں چاہیے",
        "مجھے صرف کوئی سننے والا چاہیے"
    ]

    if any(phrase in user_text for phrase in listening_phrases):
        question_mark = "؟" if language == "ur" else "?"

        # Remove a final automatically generated question.
        text = re.sub(
            rf"(?:^|\s)[^.!?؟\n]*{re.escape(question_mark)}\s*$",
            "",
            text
        ).strip()

        return text

    # ============================================================
    # URDU MODE
    # ============================================================
    if language == "ur":

        if "؟" in text:
            return text

        t = normalize_for_safety(user_message or "")

        if any(w in t for w in ["peace", "peaceful", "prayer", "praying", "allah", "dua", "namaz"]):
            q = "اس احساس میں آپ کو سب سے زیادہ سکون کس چیز سے ملا؟"

        elif any(w in t for w in ["good news", "happy", "joy", "thankful", "grateful"]):
            q = "اس لمحے کو آپ کے لیے خاص کس چیز نے بنایا؟"

        elif any(w in t for w in ["job", "work", "money", "income", "unemployed"]):
            q = "اس وقت آپ کے لیے سب سے زیادہ فوری دباؤ کس چیز کا ہے؟"

        elif any(w in t for w in ["family", "children", "parents", "responsibility"]):
            q = "اس وقت آپ کے خاندان کو کس طرح کی مدد کی سب سے زیادہ ضرورت ہے؟"

        elif any(w in t for w in ["sleep", "tired", "energy", "exhausted", "drained"]):
            q = "کیا اس کا اثر آپ کے پورے دن پر پڑ رہا ہے یا صرف کچھ خاص اوقات میں؟"

        elif any(w in t for w in ["sad", "low", "hopeless", "stress", "worried", "anxious"]):
            q = "اس وقت آپ کو سب سے زیادہ کس چیز کا بوجھ محسوس ہو رہا ہے؟"

        else:
            q = "آپ مزید کیا بات میرے ساتھ شیئر کرنا چاہیں گی؟"

        text = text.rstrip(".۔") + "\n\n" + q

        return text

       # ============================================================
    # ENGLISH MODE — NATURAL CONVERSATION
    # ============================================================

    # Do not force a follow-up question in every response.
    return text


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .core import (  # noqa: E402
    EMERGENCY_NUMBER,
    UMANG_NUMBER,
    f,
)
from .responses import (  # noqa: E402
    is_food_or_recipe_message,
    is_happy_message,
)
from .text_utils import (  # noqa: E402
    is_positive_message,
    normalize_for_safety,
)
from .words_of_hope import (  # noqa: E402
    _explicit_words_of_hope_mood,
)

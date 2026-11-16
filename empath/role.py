"""Role detection with AI: understands the user's occupation from their own words.

Keywords (detect_role in context.py) are used only as a backup."""
import json

# ─────────────────────────────────────────────
# ROLE DETECTION WITH AI (keywords are only the backup)
# One small gpt-4o-mini call, only at the role question.
# ─────────────────────────────────────────────
ROLE_CHOICES = {
    "teacher": "teaches at a school, college, madrasa or tuition centre",
    "student": "studies at a school, college, university or madrasa",
    "homemaker": "looks after the home",
    "parent": "mainly cares for children or family members",
    "unemployed": "has no job now or lost their job",
    "daily_worker": "daily-wage or manual work (labour, construction, driver, mazdoor)",
    "farmer": "farming, crops or livestock",
    "health_worker": "doctor, nurse, lady health worker or other health job",
    "business": "shopkeeper, small business or self-employed",
    "professional": "lawyer, engineer, accountant, banker, IT, journalist or other professional job",
    "government": "office job, government or private employee",
    "elderly": "retired or elderly",
    "default": "not clear from the message",
}
_ROLE_CACHE = {}


def detect_role_smart(text):
    """Understand the user's role from their own words (English, Urdu or Roman Urdu).
    Falls back to the keyword method if the AI is not available."""
    text = (text or "").strip()
    keyword_role = detect_role(text)
    if not text:
        return keyword_role

    key = text.lower()
    if key in _ROLE_CACHE:
        return _ROLE_CACHE[key]

    choices = "\n".join(f"- {k}: {v}" for k, v in ROLE_CHOICES.items())
    try:
        reply = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            max_tokens=20,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": (
                    "Read the user's message (English, Urdu or Roman Urdu) and decide what they do in life: "
                    "their occupation or role. Choose exactly one role from this list:\n" + choices + "\n"
                    "Judge the person, not places they mention: 'I am a teacher and our school was destroyed' "
                    "is teacher, not student. If more than one fits, choose the main one. "
                    'Return only JSON: {"role": "<one key from the list>"}'
                )},
                {"role": "user", "content": text[:500]},
            ],
        )
        role = str(json.loads(reply.choices[0].message.content).get("role", "")).strip().lower()
    except Exception as e:
        print("AI role detection error (using keywords):", e)
        return keyword_role

    if role not in ROLE_CHOICES:
        role = keyword_role
    elif role == "default" and keyword_role != "default":
        role = keyword_role          # the AI was unsure but a keyword is clear
    _ROLE_CACHE[key] = role
    return role


_PROFESSION_CACHE = {}


def detect_role_and_profession(text):
    """Return (role, profession_en, profession_ur).

    role: one key from ROLE_CHOICES, used inside the app (prompts, survey data).
    profession_en / profession_ur: the user's own job in a few words, e.g. ("Lawyer", "وکیل"),
    so any profession the user tells is remembered exactly, even if it is not in the list."""
    text = (text or "").strip()
    keyword_role = detect_role(text)
    if not text:
        return keyword_role, "", ""
    key = text.lower()
    if key in _PROFESSION_CACHE:
        return _PROFESSION_CACHE[key]

    choices = "\n".join(f"- {k}: {v}" for k, v in ROLE_CHOICES.items())
    try:
        reply = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            max_tokens=60,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": (
                    "Read the user's message (English, Urdu or Roman Urdu) and find what they do in life.\n"
                    "1) role: choose exactly one key from this list:\n" + choices + "\n"
                    "2) profession_en: their exact occupation in 1-3 English words with capital letters "
                    '(e.g. "Lawyer", "School Teacher", "Rickshaw Driver", "Nurse", "University Student"). '
                    'Use "" if they did not say.\n'
                    '3) profession_ur: the same in simple Urdu script (e.g. "وکیل", "اسکول ٹیچر"). "" if not said.\n'
                    "Judge the person, not places they mention: 'I am a teacher and our school was destroyed' "
                    "is a teacher. "
                    'Return only JSON: {"role": "...", "profession_en": "...", "profession_ur": "..."}'
                )},
                {"role": "user", "content": text[:500]},
            ],
        )
        data = json.loads(reply.choices[0].message.content)
        role = str(data.get("role", "")).strip().lower()
        prof_en = str(data.get("profession_en", "") or "").strip()[:40]
        prof_ur = str(data.get("profession_ur", "") or "").strip()[:40]
    except Exception as e:
        print("AI profession detection error (using keywords):", e)
        return keyword_role, "", ""

    if role not in ROLE_CHOICES:
        role = keyword_role
    elif role == "default" and keyword_role != "default":
        role = keyword_role
    result = (role, prof_en, prof_ur)
    _PROFESSION_CACHE[key] = result
    return result


def save_user_profession(user_id, profession_en, profession_ur=""):
    """Store the exact profession on the user (adds the columns the first time)."""
    if not user_id or not (profession_en or profession_ur):
        return
    try:
        db = get_db()
        cols = {r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()}
        if "profession" not in cols:
            db.execute("ALTER TABLE users ADD COLUMN profession TEXT")
        if "profession_ur" not in cols:
            db.execute("ALTER TABLE users ADD COLUMN profession_ur TEXT")
        db.execute("UPDATE users SET profession = ?, profession_ur = ? WHERE id = ?",
                   (profession_en or None, profession_ur or None, user_id))
        db.commit()
    except Exception as e:
        print("Save profession error:", e)


# Imported at the end of the file to avoid circular imports.
from .context import detect_role  # noqa: E402
from .core import client  # noqa: E402
from .database import get_db  # noqa: E402
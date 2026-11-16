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


# Imported at the end of the file to avoid circular imports.
from .context import detect_role  # noqa: E402
from .core import client  # noqa: E402
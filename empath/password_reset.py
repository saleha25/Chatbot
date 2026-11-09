"""Forgot password without sending email.

Emails in Empath AI are not verified (users can sign up with any email), so the
user proves who they are with the details they gave at signup:
    email + full name + date of birth
If all three match, they can set a new password.

Protection against guessing:
  - wrong details 5 times for one email -> that email is locked for 15 minutes
  - the error never says which detail was wrong, or whether the email exists
  - after verifying, the user has 10 minutes to set the new password
"""
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from flask import request, jsonify, session, redirect, url_for

from .core import app

MAX_ATTEMPTS = 5
LOCK_MINUTES = 15
RESET_WINDOW_MINUTES = 10

STRONG_PASSWORD = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NO_MATCH_MSG = "These details don't match our records. Please check your email, full name and date of birth."


def _now():
    return datetime.now(timezone.utc)


# Urdu/Arabic letters that look the same but have different codes on different keyboards
_URDU_LETTER_MAP = str.maketrans({
    "ي": "ی",  # Arabic yeh  ي -> Urdu yeh ی
    "ى": "ی",  # alef maksura ى -> ی
    "ك": "ک",  # Arabic kaf ك -> Urdu kaf ک
    "ه": "ہ",  # Arabic heh ه -> Urdu heh ہ
    "ة": "ہ",  # teh marbuta ة -> ہ
    "ۃ": "ہ",  # ۃ -> ہ
    "ۂ": "ہ",  # ۂ -> ہ
})
# Harakat (zabar, zer, pesh...), tatweel and invisible direction marks
_URDU_IGNORE_RE = re.compile(r"[ً-ٰٟـ​-‏‪-‮⁦-⁩]")


def _norm_name(name):
    # "  sara   KHAN " -> "sara khan";  Urdu: same letters whatever keyboard was used
    text = unicodedata.normalize("NFC", name or "")
    text = _URDU_IGNORE_RE.sub("", text).translate(_URDU_LETTER_MAP)
    return re.sub(r"\s+", " ", text.strip()).lower()


def _name_matches(typed, row):
    """Accept the English name OR the Urdu name saved for this account."""
    if not typed:
        return False
    candidates = [row["full_name"]]
    try:
        candidates.append(row["urdu_name"])
    except (IndexError, KeyError):
        pass
    for saved in candidates:
        saved = _norm_name(saved)
        if saved and (typed == saved or typed.replace(" ", "") == saved.replace(" ", "")):
            return True
    return False


def _ensure_table(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_attempts (
            email TEXT PRIMARY KEY,
            failed INTEGER DEFAULT 0,
            locked_until TEXT
        )
        """
    )
    db.commit()


def _locked_minutes_left(db, email):
    row = db.execute(
        "SELECT locked_until FROM password_reset_attempts WHERE email = ?", (email,)
    ).fetchone()
    if row and row["locked_until"]:
        until = datetime.fromisoformat(row["locked_until"])
        if _now() < until:
            return max(1, int((until - _now()).total_seconds() // 60) + 1)
        db.execute("DELETE FROM password_reset_attempts WHERE email = ?", (email,))
        db.commit()
    return 0


def _record_failure(db, email):
    db.execute(
        """
        INSERT INTO password_reset_attempts (email, failed) VALUES (?, 1)
        ON CONFLICT(email) DO UPDATE SET failed = failed + 1
        """,
        (email,),
    )
    failed = db.execute(
        "SELECT failed FROM password_reset_attempts WHERE email = ?", (email,)
    ).fetchone()["failed"]
    if failed >= MAX_ATTEMPTS:
        db.execute(
            "UPDATE password_reset_attempts SET locked_until = ? WHERE email = ?",
            ((_now() + timedelta(minutes=LOCK_MINUTES)).isoformat(), email),
        )
    db.commit()
    return MAX_ATTEMPTS - failed


@app.route("/forgot-password")
def forgot_password_page():
    # The forgot-password form lives inside the login card (auth.html)
    if session.get("account_id"):
        return redirect(url_for("dashboard"))
    return redirect(url_for("auth_page", tab="forgot"))


@app.route("/forgot-password/verify", methods=["POST"])
def forgot_password_verify():
    init_db()
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    name = _norm_name(data.get("name"))
    dob = (data.get("dob") or "").strip()

    # "code" lets the page show the message in Urdu or English
    if not email or not name or not dob:
        return jsonify({"error": "Please fill in all fields.", "code": "fill_all"}), 400
    if not EMAIL_RE.match(email):
        return jsonify({"error": "Please enter a valid email address.", "code": "bad_email"}), 400

    db = get_db()
    _ensure_table(db)

    wait = _locked_minutes_left(db, email)
    if wait:
        return jsonify({
            "error": f"Too many wrong attempts. Please try again in {wait} minute(s).",
            "code": "locked", "minutes": wait,
        }), 429

    row = db.execute(
        "SELECT * FROM accounts WHERE LOWER(email) = ?", (email,)
    ).fetchone()

    if not row or not _name_matches(name, row) or (row["dob"] or "").strip() != dob:
        left = _record_failure(db, email)
        if left <= 0:
            return jsonify({
                "error": f"Too many wrong attempts. Please try again in {LOCK_MINUTES} minutes.",
                "code": "locked", "minutes": LOCK_MINUTES,
            }), 429
        return jsonify({
            "error": f"{NO_MATCH_MSG} {left} attempt(s) left.",
            "code": "no_match", "left": left,
        }), 400

    db.execute("DELETE FROM password_reset_attempts WHERE email = ?", (email,))
    db.commit()
    session["reset_account_id"] = row["id"]
    session["reset_until"] = (_now() + timedelta(minutes=RESET_WINDOW_MINUTES)).isoformat()
    return jsonify({"message": "Details verified. Please choose a new password."}), 200


@app.route("/forgot-password/reset", methods=["POST"])
def forgot_password_reset():
    init_db()
    data = request.get_json(silent=True) or {}
    password = data.get("password") or ""
    confirm = data.get("confirm") or ""

    account_id = session.get("reset_account_id")
    until = session.get("reset_until")
    if not account_id or not until or _now() > datetime.fromisoformat(until):
        session.pop("reset_account_id", None)
        session.pop("reset_until", None)
        return jsonify({"error": "Your reset session has expired. Please start again.", "code": "expired"}), 400

    if password != confirm:
        return jsonify({"error": "Passwords do not match.", "code": "mismatch"}), 400

    if not STRONG_PASSWORD.match(password):
        return jsonify({
            "error": (
                "Password must contain at least 8 characters, one uppercase letter, "
                "one lowercase letter, one number and one special character."
            ),
            "code": "weak",
        }), 400

    db = get_db()
    db.execute(
        "UPDATE accounts SET password_hash = ? WHERE id = ?",
        (hash_password(password), account_id),
    )
    db.commit()

    session.pop("reset_account_id", None)
    session.pop("reset_until", None)
    return jsonify({"message": "Your password has been updated. You can now log in."}), 200


from .database import get_db, hash_password, init_db  # noqa: E402
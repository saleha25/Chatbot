"""Pages and routes: intro, login, signup, logout, delete account, dashboard.

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


@app.route("/")
def index():
    return render_template("intro.html")

@app.route("/login")
def auth_page():
    if session.get("account_id"):
        return redirect(url_for("dashboard"))
    return render_template("auth.html")




@app.route("/signup", methods=["POST"])
def signup():
    init_db()
    data = request.get_json(silent=True) or {}

    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    dob = data.get("dob") or ""

    # 1. Validate required fields.
    if not name or not email or not password or not dob:
        return jsonify({
            "error": "All fields are required."
        }), 400

    # 2. Validate email format.
    email = email.strip().lower()

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({
            "error": "Please enter a valid email address."
        }), 400

    # 3. Strong password validation.
    strong_password = re.compile(
        r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$"
    )

    if not strong_password.match(password):
        return jsonify({
            "error": (
                "Password must contain at least 8 characters, "
                "one uppercase letter, one lowercase letter, "
                "one number and one special character."
            )
        }), 400

    # 4. Validate date of birth and minimum age.
    try:
        birth = datetime.strptime(dob, "%Y-%m-%d")
        today = datetime.today()

        age = today.year - birth.year - (
            (today.month, today.day) < (birth.month, birth.day)
        )

        if age < 18:
            return jsonify({
                "error": f"You must be 18 or older. You are {age} years old."
            }), 400

    except ValueError:
        return jsonify({
            "error": "Invalid date of birth."
        }), 400

    db = get_db()
    cursor = db.cursor()

    # 5. Check whether this email already has an account.
    cursor.execute(
        """
        SELECT id, full_name, dob, password_hash
        FROM accounts
        WHERE LOWER(email) = ?
        """,
        (email,)
    )

    existing_account = cursor.fetchone()

    # 6. Handle an existing account.
    if existing_account:

        # Reject if password does not match existing account.
        if not verify_password(
            password,
            existing_account["password_hash"]
        ):
            return jsonify({
                "error": "Email already exists. Please login."
            }), 409

        if (
            existing_account["full_name"] != name
            or existing_account["dob"] != dob
        ):
            return jsonify({
                "error": "Your account details do not match the existing account."
            }), 400

        account_id = existing_account["id"]
        profile_id = f"EMPATH_{account_id}"

        try:
            profile_result = create_mygnuhealth_profile(
                profile_id,
                name,
                dob,
                password
            )

            if not profile_result or not profile_result.get("success"):
                app.logger.error(
                    "MyGNUHealth recovery failed for %s: %s",
                    profile_id,
                    profile_result
                )

                return jsonify({
                    "error": "Local MyGNUHealth setup could not be completed. Please try again later."
                }), 500

            existing_user = get_user_by_account_id(account_id)

            if existing_user:
                user_id = existing_user["id"]
            else:
                user_id = create_user(
                    name,
                    account_id=account_id
                )

            if not user_id or not set_gnuhealth_id(user_id, profile_id):
                raise RuntimeError(
                    "Unable to link local MyGNUHealth profile."
                )

        except Exception:
            app.logger.exception(
                "Signup recovery failed for account %s",
                account_id
            )

            return jsonify({
                "error": "Account recovery could not be completed. Please contact support."
            }), 500

        # Sign in after successful recovery.
        session.permanent = True
        session["account_id"] = account_id
        session["account_name"] = name
        session["user_id"] = user_id

        return jsonify({
            "message": "Your existing account and MyGNUHealth profile are ready.",
            "name": name,
            "mygnuhealth_profile_id": profile_id,
            "gnuhealth_id": profile_id
        }), 200

    # 7. Create a new Empath AI account.
    try:
        pw_hash = hash_password(password)

        cursor.execute(
            """
            INSERT INTO accounts (full_name, email, password_hash, dob)
            VALUES (?, ?, ?, ?)
            """,
            (name, email, pw_hash, dob)
        )

        db.commit()
        account_id = cursor.lastrowid

    except Exception:
        db.rollback()
        app.logger.exception("Failed to create local account")

        # Handle duplicate email safely.
        cursor.execute(
            "SELECT id FROM accounts WHERE LOWER(email) = ?",
            (email,)
        )

        if cursor.fetchone():
            return jsonify({
                "error": "Email already exists. Please login."
            }), 409

        return jsonify({
            "error": "Could not create your account. Please try again."
        }), 500

    # 8. Generate a unique local MyGNUHealth profile ID.
    profile_id = f"EMPATH_{account_id}"

    # 9. Initialize the separate local MyGNUHealth profile.
    try:
        profile_result = create_mygnuhealth_profile(
            profile_id,
            name,
            dob,
            password
        )

        if not profile_result or not profile_result.get("success"):
            app.logger.error(
                "MyGNUHealth initialization failed for %s: %s",
                profile_id,
                profile_result
            )

            return jsonify({
                "error": (
                    "Your Empath AI account was created, but your local "
                    "MyGNUHealth profile could not be initialized. "
                    "Please retry signup using the same details."
                ),
                "mygnuhealth_profile_id": profile_id
            }), 500

    except Exception:
        app.logger.exception(
            "MyGNUHealth profile creation failed for %s",
            profile_id
        )

        return jsonify({
            "error": (
                "Your Empath AI account was created, but your local "
                "MyGNUHealth profile could not be initialized. "
                "Please retry signup using the same details."
            ),
            "mygnuhealth_profile_id": profile_id
        }), 500

    app.logger.info(
        "Local MyGNUHealth profile initialized for %s",
        profile_id
    )

    # 10. Create or retrieve the local Empath AI user.
    try:
        existing_user = get_user_by_account_id(account_id)

        if existing_user:
            user_id = existing_user["id"]
        else:
            user_id = create_user(
                name,
                account_id=account_id
            )

        if not user_id:
            raise RuntimeError("Could not create or retrieve local user.")

        # 11. Link the MyGNUHealth profile to the correct user.
        if not set_gnuhealth_id(user_id, profile_id):
            raise RuntimeError(
                "Could not link local MyGNUHealth profile to user."
            )

    except Exception:
        app.logger.exception(
            "Local account linking failed for profile %s",
            profile_id
        )

        return jsonify({
            "error": (
                "Your MyGNUHealth profile was initialized, but "
                "linking it to your Empath AI account failed. "
                "Please retry signup using the same details."
            ),
            "mygnuhealth_profile_id": profile_id
        }), 500

    app.logger.info(
        "MyGNUHealth profile %s linked to Empath AI user %s",
        profile_id,
        user_id
    )

    # 12. Sign in after successful account creation and linking.
    session.permanent = True
    session["account_id"] = account_id
    session["account_name"] = name
    session["user_id"] = user_id

    return jsonify({
        "message": (
            "Account created and connected to your local "
            "MyGNUHealth profile."
        ),
        "name": name,
        "mygnuhealth_profile_id": profile_id,
        "gnuhealth_id": profile_id
    }), 201



@app.route("/login", methods=["POST"])
def login():
    init_db()

    data = request.get_json(silent=True) or {}

    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({
            "error": "Email and password are required."
        }), 400

    db = get_db()
    cursor = db.cursor()

    # Find account using email
    cursor.execute(
        """
        SELECT id, full_name, password_hash
        FROM accounts
        WHERE LOWER(email) = ?
        """,
        (email,)
    )

    row = cursor.fetchone()

    # Verify existing and new password hashes
    if not row or not verify_password(
        password,
        row["password_hash"]
    ):
        return jsonify({
            "error": "Invalid email or password. Please check your credentials."
        }), 401

    session.permanent = True
    session["account_id"] = row["id"]
    session["account_name"] = row["full_name"]

    # Restore existing Empath AI user
    existing_user = get_user_by_account_id(row["id"])

    if existing_user:
        session["user_id"] = existing_user["id"]

    return jsonify({
        "message": "Logged in.",
        "name": row["full_name"]
    }), 200


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))
@app.route("/delete_account", methods=["POST"])
def delete_account():
    print("DELETE ACCOUNT ROUTE HIT")

    account_id = session.get("account_id")
    if not account_id:
        return jsonify({
            "success": False,
            "message": "No logged-in account found."
        }), 401

    db = get_db()
    user = get_user_by_account_id(account_id)

    try:
        if user:
            db.execute("DELETE FROM wellbeing_logs WHERE user_id = ?", (user["id"],))
            db.execute("DELETE FROM chat_history WHERE user_id = ?", (user["id"],))
            db.execute("DELETE FROM assessments WHERE user_id = ?", (user["id"],))
            db.execute("DELETE FROM users WHERE id = ?", (user["id"],))

        db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        db.commit()
        session.clear()

        print("ACCOUNT DELETED SUCCESSFULLY:", account_id)

        return jsonify({
            "success": True,
            "message": "Your account has been deleted successfully.",
            "redirect": url_for("index")
        }), 200

    except Exception as e:
        db.rollback()
        print("DELETE ACCOUNT ERROR:", str(e))
        return jsonify({
            "success": False,
            "message": f"Could not delete account: {str(e)}"
        }), 500

@app.route("/dashboard")
@login_required
def dashboard():

    account_id = session.get("account_id")
    english_name = session.get("account_name", "")

    urdu_name = get_urdu_name_for_account(account_id)

    return render_template(
        "dashboard.html",
        user_name=english_name,
        user_name_urdu=urdu_name
    )


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .database import (  # noqa: E402
    create_user,
    get_db,
    get_urdu_name_for_account,
    get_user_by_account_id,
    hash_password,
    init_db,
    set_gnuhealth_id,
    verify_password,
)

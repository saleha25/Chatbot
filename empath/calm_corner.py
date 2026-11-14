"""Calm Corner: guided breathing and grounding exercises.

A separate page. It does not touch the chat or any other feature.
The Urdu voice uses the existing /tts/urdu route; English uses the browser's voice.
Each finished exercise (distress before / after) is also saved, so it can appear in the Wellbeing Report.
"""
from flask import jsonify, render_template, request, session

from .core import app, login_required

CALM_EXERCISE_KEYS = {"calm", "box", "grounding", "nightmare"}


@app.route("/calm_corner")
@login_required
def calm_corner():
    return render_template(
        "calm_corner.html",
        user_key=str(session.get("user_id") or "guest"),
    )


@app.route("/calm_corner/log", methods=["POST"])
@login_required
def calm_corner_log():
    """Save one finished exercise. Called quietly by the page; the page works the same if this fails."""
    data = request.get_json(silent=True) or {}
    exercise = str(data.get("exercise", "")).strip()
    try:
        before = int(data.get("before"))
        after = int(data.get("after"))
    except (TypeError, ValueError):
        return jsonify({"ok": False}), 400
    if exercise not in CALM_EXERCISE_KEYS or not (0 <= before <= 10 and 0 <= after <= 10):
        return jsonify({"ok": False}), 400
    try:
        ensure_report_tables()
        db = get_db()
        db.execute(
            "INSERT INTO calm_sessions (user_id, account_id, exercise, before_score, after_score) VALUES (?, ?, ?, ?, ?)",
            (session.get("user_id"), session.get("account_id"), exercise, before, after),
        )
        db.commit()
    except Exception as e:
        print("Calm Corner log error:", e)
        return jsonify({"ok": False}), 500
    return jsonify({"ok": True})


# Imported at the end of the file to avoid circular imports.
from .database import get_db  # noqa: E402
from .report import ensure_report_tables  # noqa: E402
"""Calm Corner: guided breathing and grounding exercises.

A separate page. It does not touch the chat, the database or any other feature.
The Urdu voice uses the existing /tts/urdu route; English uses the browser's voice.
"""
from flask import render_template, session

from .core import app, login_required


@app.route("/calm_corner")
@login_required
def calm_corner():
    return render_template(
        "calm_corner.html",
        user_key=str(session.get("user_id") or "guest"),
    )
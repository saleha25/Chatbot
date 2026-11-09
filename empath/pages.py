"""Other pages: wellness, Quranic corner, Urdu text-to-speech.

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



# Wellness page shows only the most recent points; the history page shows everything.
WELLNESS_LATEST_POINTS = 7       # last 7 entries in each chart (sleep, mood, energy)
WELLNESS_LATEST_ASSESSMENTS = 5  # last 5 weekly check-ins


@app.route("/wellness")
@login_required
def wellness():
    return _render_wellness(show_all=False)


MOOD_LEVEL = {"very_low": 1, "very low": 1, "low": 2, "anxious": 2, "okay": 3, "good": 4, "better": 4}
ENERGY_LEVEL = {"very_low": 1, "very low": 1, "low": 2, "medium": 3, "high": 4}


@app.route("/wellness/history")
@login_required
def wellness_history():
    """Full history as a day-by-day timeline (separate design from the Wellness page)."""
    db = get_db()
    user_id = session.get("user_id")

    wellbeing_rows = db.execute(
        "SELECT * FROM wellbeing_logs WHERE user_id = ? ORDER BY log_date DESC, id DESC",
        (user_id,),
    ).fetchall()
    assessment_rows = db.execute(
        "SELECT * FROM assessments WHERE user_id = ? ORDER BY assessment_date DESC",
        (user_id,),
    ).fetchall()

    def to10(value, max_score):
        return round((float(value) / max_score) * 10, 1) if value is not None else 0

    entries = []  # (sort_key, date, entry)
    for r in wellbeing_rows:
        if r["sleep_hours"] is None and not r["mood"] and not r["energy"]:
            continue
        raw = str(r["log_date"] or "")
        mood = str(r["mood"]).lower() if r["mood"] else ""
        energy = str(r["energy"]).lower() if r["energy"] else ""
        has = []
        if r["sleep_hours"] is not None:
            has.append("sleep")
        if mood:
            has.append("mood")
        if energy:
            has.append("energy")
        entries.append((raw, raw[:10], {
            "type": "chat",
            "time": raw[11:16],
            "sleep": f'{float(r["sleep_hours"]):g}' if r["sleep_hours"] is not None else "",
            "mood": mood.replace("_", " ").title(),
            "mood_level": MOOD_LEVEL.get(mood, 2),
            "energy": energy.replace("_", " ").title(),
            "energy_level": ENERGY_LEVEL.get(energy, 2),
            "has": has,
        }))

    for r in assessment_rows:
        raw = str(r["assessment_date"] or "")
        entries.append((raw, raw[:10], {
            "type": "assessment",
            "phq10": to10(r["phq9_total"], 27),
            "gad10": to10(r["gad7_total"], 21),
            "who10": to10(r["who5_total"], 25),
            "severity": r["severity"] or "—",
            "has": ["assessment"],
        }))

    entries.sort(key=lambda e: e[0], reverse=True)

    # Group: month -> day -> entries (newest first)
    timeline = []
    for _, day_str, entry in entries:
        try:
            d = datetime.strptime(day_str, "%Y-%m-%d")
        except ValueError:
            continue
        month_key = d.strftime("%Y-%m")
        if not timeline or timeline[-1]["key"] != month_key:
            timeline.append({"key": month_key, "label": d.strftime("%B %Y"), "days": []})
        days = timeline[-1]["days"]
        if not days or days[-1]["date"] != day_str:
            days.append({
                "date": day_str,
                "day": d.day,
                "weekday": d.weekday(),
                "weekday_label": d.strftime("%a"),
                "entries": [],
                "has": [],
            })
        days[-1]["entries"].append(entry)
        for h in entry["has"]:
            if h not in days[-1]["has"]:
                days[-1]["has"].append(h)

    all_days = [day["date"] for month in timeline for day in month["days"]]
    stats = {
        "days_tracked": len(all_days),
        "chat_entries": sum(1 for e in entries if e[2]["type"] == "chat"),
        "assessments": sum(1 for e in entries if e[2]["type"] == "assessment"),
        "first_date": min(all_days) if all_days else "—",
    }

    return render_template(
        "wellness_history.html",
        timeline=timeline,
        stats=stats,
        has_data=bool(timeline),
        user_name_urdu=get_urdu_name_for_account(session.get("account_id")),
    )


def _last(items, n):
    return list(items[-n:]) if n else list(items)


def _render_wellness(show_all=False):
    db = get_db()
    user_id = session.get("user_id")

    wellbeing_rows = db.execute("""
        SELECT * FROM wellbeing_logs
        WHERE user_id = ?
        ORDER BY log_date ASC
    """, (user_id,)).fetchall()

    assessment_rows_raw = db.execute("""
        SELECT * FROM assessments
        WHERE user_id = ?
        ORDER BY assessment_date ASC
    """, (user_id,)).fetchall()

    # ---- SUMMARY ----
    latest_sleep = "-"

    if wellbeing_rows:
      for r in reversed(wellbeing_rows):
        if r["sleep_hours"] is not None:
            latest_sleep = float(r["sleep_hours"])
            break
   

    latest_mood = "-"
    latest_energy = "-"
    if wellbeing_rows:
        for r in reversed(wellbeing_rows):
            if latest_mood == "-" and r["mood"]:
                latest_mood = str(r["mood"]).replace("_", " ").title()
            if latest_energy == "-" and r["energy"]:
                latest_energy = str(r["energy"]).replace("_", " ").title()
            if latest_mood != "-" and latest_energy != "-":
                break

    # ---- CHART DATA ----
    sleep_labels = []
    sleep_values = []
    sleep_colors = []

    mood_labels = []
    mood_values = []

    energy_labels = []
    energy_values = []

    for r in wellbeing_rows:
        date = str(r["log_date"])[:10]

        if r["sleep_hours"] is not None:
            sleep_labels.append(date)
            sleep_values.append(float(r["sleep_hours"]))

            if float(r["sleep_hours"]) >= 7:
                sleep_colors.append("green")
            elif float(r["sleep_hours"]) >= 5:
                sleep_colors.append("orange")
            else:
                sleep_colors.append("red")

        if r["mood"]:
            mood_labels.append(date)
            mood_values.append({
                "very_low": 1,
                "low": 2,
                "anxious": 2,
                "okay": 3,
                "good": 4,
                "better": 4
            }.get(str(r["mood"]).lower(), 2))

        if r["energy"]:
            energy_labels.append(date)
            energy_values.append({
                "very_low": 1,
                "low": 2,
                "medium": 3,
                "high": 4
            }.get(str(r["energy"]).lower(), 2))

    # ---- ASSESSMENT CHART DATA ----
    assessment_labels = []
    phq = []
    gad = []
    who = []

    for r in assessment_rows_raw:
        assessment_labels.append(str(r["assessment_date"])[:10])
        phq.append(round((float(r["phq9_total"]) / 27) * 10, 1) if r["phq9_total"] is not None else 0)
        gad.append(round((float(r["gad7_total"]) / 21) * 10, 1) if r["gad7_total"] is not None else 0)
        who.append(round((float(r["who5_total"]) / 25) * 10, 1) if r["who5_total"] is not None else 0)

    # ---- TABLE DATA ----
    assessment_rows = []
    for r in assessment_rows_raw:
        assessment_rows.append({
            "display_date": str(r["assessment_date"])[:10],
            "phq10": round((float(r["phq9_total"]) / 27) * 10, 1) if r["phq9_total"] is not None else 0,
            "gad10": round((float(r["gad7_total"]) / 21) * 10, 1) if r["gad7_total"] is not None else 0,
            "who10": round((float(r["who5_total"]) / 25) * 10, 1) if r["who5_total"] is not None else 0,
            "severity": r["severity"] or "—",
            "emotion_detected": r["emotion_detected"] or "—"
        })

    # ---- DAY-BY-DAY RECORDS (full history page only), newest first ----
    wellbeing_records = []
    if show_all:
        for r in reversed(wellbeing_rows):
            if r["sleep_hours"] is None and not r["mood"] and not r["energy"]:
                continue
            raw_date = str(r["log_date"] or "")
            wellbeing_records.append({
                "date": raw_date[:10],
                "time": raw_date[11:16],
                "sleep": (f'{float(r["sleep_hours"]):g}' if r["sleep_hours"] is not None else ""),
                "mood": (str(r["mood"]).replace("_", " ").title() if r["mood"] else ""),
                "energy": (str(r["energy"]).replace("_", " ").title() if r["energy"] else ""),
            })

    # ---- LATEST vs FULL HISTORY ----
    total_counts = {
        "sleep": len(sleep_values),
        "mood": len(mood_values),
        "energy": len(energy_values),
        "assessments": len(assessment_labels),
    }
    if not show_all:
        n = WELLNESS_LATEST_POINTS
        sleep_labels, sleep_values, sleep_colors = _last(sleep_labels, n), _last(sleep_values, n), _last(sleep_colors, n)
        mood_labels, mood_values = _last(mood_labels, n), _last(mood_values, n)
        energy_labels, energy_values = _last(energy_labels, n), _last(energy_values, n)
        a = WELLNESS_LATEST_ASSESSMENTS
        assessment_labels, phq, gad, who = _last(assessment_labels, a), _last(phq, a), _last(gad, a), _last(who, a)
        assessment_rows = _last(assessment_rows, a)

    return render_template(
        "wellness.html",
        view_mode="all" if show_all else "latest",
        wellbeing_records=wellbeing_records,
        total_counts=total_counts,
        latest_points=WELLNESS_LATEST_POINTS,
        latest_assessments=WELLNESS_LATEST_ASSESSMENTS,
        summary={
            "avg_sleep": latest_sleep,
            "latest_mood": latest_mood,
            "latest_energy": latest_energy,
            "total_assessments": len(assessment_rows_raw)
        },
        chart_data={
            "sleep_labels": sleep_labels,
            "sleep_values": sleep_values,
            "sleep_colors": sleep_colors,

            "mood_labels": mood_labels,
            "mood_values": mood_values,
            "mood_colors": [
                "#d9534f" if v == 1 else
                "#f0ad4e" if v == 2 else
                "#5bc0de" if v == 3 else
                "#5cb85c"
                for v in mood_values
            ],

            "energy_labels": energy_labels,
            "energy_values": energy_values,
            "energy_colors": [
                "#d9534f" if v == 1 else
                "#f0ad4e" if v == 2 else
                "#5bc0de" if v == 3 else
                "#5cb85c"
                for v in energy_values
            ],

            "assessment_labels": assessment_labels,
            "phq_values": phq,
            "gad_values": gad,
            "who_values": who
        },
        assessment_rows=assessment_rows[::-1],
        has_data=bool(wellbeing_rows or assessment_rows_raw),
        user_name_urdu=get_urdu_name_for_account(session.get("account_id"))
    )
    
@app.route("/quranic_corner")
@login_required
def quranic_corner():
    return render_template("quranic_corner.html")
    


@app.route("/api/quranic_corner/verses", methods=["POST"])
@login_required
def api_quranic_corner_verses():
    try:
        data = request.get_json(silent=True) or {}

        category = str(data.get("category", "")).strip().lower()
        language = str(data.get("language", "en")).strip().lower()
        count = data.get("count", 3)
        used_ids = data.get("used_ids", [])

        allowed_categories = {
            "anxiety",
            "sadness",
            "hopelessness",
            "fear",
            "loneliness",
            "gratitude"
        }

        if category not in allowed_categories:
            return jsonify({
                "success": False,
                "message": "Invalid category."
            }), 400

        if language not in ("en", "ur"):
            return jsonify({
                "success": False,
                "message": "Invalid language."
            }), 400

        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 1
            or count > 3
        ):
            return jsonify({
                "success": False,
                "message": "Invalid Ayat count."
            }), 400

        if not isinstance(used_ids, list):
            used_ids = []

        # JSON file path
        json_path = os.path.join(
            BASE_PATH,
            "data",
            "quranic_comfort_content.json"
        )

        # Load JSON content
        with open(json_path, "r", encoding="utf-8") as f:
            content_data = json.load(f)

        # Debug information
        print("\n========== QURANIC JSON DEBUG ==========")
        print("JSON FILE:", os.path.abspath(json_path))
        print("AVAILABLE JSON KEYS:", list(content_data.keys()))

        print("\n========== CATEGORY DEBUG ==========")
        print("SELECTED CATEGORY:", category)
        print("TOTAL VERSES:", len(content_data.get("verses", [])))
        print("CATEGORY DATA:", content_data.get("categories", {}).keys())
        print("====================================\n")

        # Get selected Ayat
        verses = get_category_verses(
            json_path,
            category,
            count=count,
            used_ids=used_ids
        )

        print("RETURNED VERSES:", len(verses))
        print("RETURNED VERSE DATA:", verses)

        if not verses:
            return jsonify({
                "success": False,
                "message": "No Ayat available in this category yet."
            }), 404

        # Attach additional content for ALL categories
        category_content = content_data.get(
            f"{category}_content", {}
        )

        print("SELECTED CATEGORY:", category)
        print("CATEGORY CONTENT ENTRIES:", len(category_content))

        for verse in verses:
            verse_id = verse.get("id")
            extra_content = category_content.get(verse_id, {})

            print("SELECTED VERSE:", verse_id)
            print("MATCHED CONTENT:", bool(extra_content))
            print("CONTENT FIELDS:", list(extra_content.keys()))

            if extra_content:
                verse.update(extra_content)

        print("========================================\n")

        return jsonify({
            "success": True,
            "category": category,
            "language": language,
            "verses": verses
        })

    except Exception:
        app.logger.exception(
            "Quranic Comfort Corner API error"
        )

        return jsonify({
            "success": False,
            "message": "Unable to load Quranic guidance."
        }), 500



@app.route("/tts/urdu", methods=["POST"])
@login_required
def urdu_tts():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Text is required"}), 400

    if len(text) > 3000:
        return jsonify({"error": "Text is too long"}), 400

    async def generate_audio():
        audio_buffer = io.BytesIO()
        communicate = edge_tts.Communicate(
            text,
            voice="ur-PK-UzmaNeural"
        )

        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_buffer.write(chunk["data"])

        audio_buffer.seek(0)
        return audio_buffer

    try:
        audio = asyncio.run(generate_audio())
        return send_file(audio, mimetype="audio/mpeg")
    except Exception as e:
        print("Urdu TTS error:", e)
        return jsonify({"error": "Urdu voice could not be generated"}), 500  


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .core import (  # noqa: E402
    BASE_PATH,
    f,
)
from .database import (  # noqa: E402
    get_db,
    get_urdu_name_for_account,
)

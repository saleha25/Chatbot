"""Weekly self-assessment (PHQ-9, GAD-7, WHO-5): questions, scoring, advice, routes.

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
)



def generate_assessment_advice(phq_total, gad_total, who_total, phq_q9=0, prev=None):
    """Returns (advice_text, risk_level). risk_level: 'safe' | 'moderate' | 'high' | 'crisis'"""
    phq_sev = sa_get_phq9_severity(phq_total)[0]
    gad_sev = sa_get_gad7_severity(gad_total)[0]
    who_pct = who_total * 4
    risk_level = "safe"

    if phq_q9 >= 2 or phq_total >= 20:
        risk_level = "crisis"
    elif phq_total >= 15 or gad_total >= 15:
        risk_level = "high"
    elif phq_total >= 10 or gad_total >= 10:
        risk_level = "moderate"

    if gad_total >= 18 and phq_total >= 15 and who_pct <= 20:
        risk_level = "crisis"

    intro = (
        "Thank you for taking a moment to check in with yourself. "
        "That matters more than it may seem, and it helps show how things have been feeling lately."
    )

    if risk_level == "crisis":
        intro = (
            "Thank you for answering these questions honestly. "
            "From your answers, it looks like things may feel very heavy right now, so I want to speak gently and clearly with you."
        )
    elif risk_level == "high":
        intro = (
            "Thank you for being honest in your answers. "
            "It looks like you have been carrying a lot recently, and that kind of weight can make everyday life feel much harder."
        )
    elif risk_level == "moderate":
        intro = (
            "Thank you for checking in with yourself. "
            "Your answers suggest that some things have been weighing on you, even if not every moment feels equally heavy."
        )

    mood_line = ""
    if phq_sev == "Minimal":
        mood_line = (
            "Your mood score does not look deeply heavy right now, and that is a good sign. "
            "Even so, it is still worth protecting your routine, your rest, and your connection with people who help you feel steady."
        )
    elif phq_sev == "Mild":
        mood_line = (
            "It seems there is some sadness or emotional heaviness sitting quietly in the background. "
            "That does not mean you are weak — it may simply mean your heart needs a little more care than usual these days."
        )
    elif phq_sev == "Moderate":
        mood_line = (
            "Your answers suggest that sadness has been affecting you in a more noticeable way. "
            "When that happens, even simple things can start to feel harder, so it is important to be gentle with yourself and not ignore it."
        )
    else:
        mood_line = (
            "Your answers suggest that your emotional pain may be quite intense right now. "
            "If everything feels overwhelming, please remember that this is exactly the kind of time when support matters most."
        )

    anxiety_line = ""
    if gad_sev == "Mild":
        anxiety_line = (
            "There also seems to be some worry building up inside you. "
            "That kind of anxious tension can quietly drain your energy, especially when life already feels uncertain."
        )
    elif gad_sev == "Moderate":
        anxiety_line = (
            "It looks like worry has been taking up real space in your mind. "
            "That can leave a person feeling restless, tense, and unable to fully settle, even when they want to."
        )
    elif gad_sev == "Severe":
        anxiety_line = (
            "Your answers suggest that worry and inner tension may be hitting you very strongly right now. "
            "When anxiety reaches that level, it can affect sleep, concentration, and even the body itself."
        )

    wellbeing_line = ""
    if who_pct < 28:
        wellbeing_line = (
            "Your wellbeing score also looks quite low, which can make the day feel flat, tiring, or emotionally empty. "
            "If that is how it has been feeling, please know that it makes sense to feel worn down."
        )
    elif who_pct < 52:
        wellbeing_line = (
            "Your wellbeing seems a little low too, which can show up as reduced energy, less motivation, or feeling disconnected from the day. "
            "That does not mean something is wrong with you — it may simply mean you have been carrying too much for too long."
        )
    else:
        wellbeing_line = (
            "Your wellbeing score is not extremely low, which is encouraging. "
            "That means there is still some strength to build on, even if things do not feel fully settled right now."
        )

    guidance_parts = []

    if risk_level == "crisis":
        guidance_parts.append(
            "Right now, please do not stay alone with thoughts of harming yourself. "
            "Reach out to someone near you and call Umang (0311-7786264) or 1122 as soon as possible."
        )
    elif risk_level == "high":
        guidance_parts.append(
            "If you can, please talk to someone you trust and consider speaking with a mental health professional or doctor soon. "
            "You do not have to wait until things get even worse before asking for support."
        )
    elif risk_level == "moderate":
        guidance_parts.append(
            "This may be a good time to slow things down and care for yourself a bit more intentionally. "
            "Small things really do count here — steady sleep, a little fresh air, one trusted conversation, and fewer long hours alone."
        )
    else:
        guidance_parts.append(
            "Even though your scores are not showing a severe level right now, it is still worth taking your feelings seriously. "
            "Little habits done consistently can protect you before things quietly build up."
        )

    if gad_total >= 5:
        guidance_parts.append(
            "When worry starts building, try one very simple thing first: breathe in for 4, hold for 4, and breathe out for 6 a few times. "
            "It will not solve everything, but it can help your body feel a little safer."
        )

    if who_pct < 52:
        guidance_parts.append(
            "Try not to pressure yourself into doing a lot at once. "
            "A short walk, sitting outside for a little while, proper food, or a few quiet minutes with someone safe can be enough for one day."
        )

          # compare with previous assessment
    if prev and prev["phq9_total"] is not None:
        chg = phq_total - prev["phq9_total"]

        if chg >= 5:
            guidance_parts.append(
                f"Compared with your last check-in, your depression score has gone up by {chg} points. "
                "That suggests things may have become heavier recently, so this is a good time to be extra kind to yourself and not ignore the shift."
            )
        elif chg <= -5:
            guidance_parts.append(
                f"Compared with your last check-in, your depression score has improved by {abs(chg)} points. "
                "That is meaningful progress, even if you do not feel completely okay yet."
            )

    closing = (
        "Please remember this: the way you feel right now is important, but it is not the whole story of your life. "
        "If things start feeling too heavy, come back to the main chat and talk, and if it feels urgent, please call Umang (0311-7786264) or 1122."
    )

    parts = [intro, mood_line]

    if anxiety_line:
        parts.append(anxiety_line)

    if wellbeing_line:
        parts.append(wellbeing_line)

    parts.extend(guidance_parts)
    parts.append(closing)

    
    advice_text = "\n\n".join([p for p in parts if p])

    
    language = session.get(
        "sa_language",
        session.get("language", "en")
    )

    if language == "ur":

        # Urdu introduction based on risk level
        if risk_level == "crisis":
            intro_ur = (
                "آپ کے جوابات سے لگتا ہے کہ اس وقت آپ کے لیے حالات کافی مشکل ہو سکتے ہیں۔ "
                "آپ نے ایمانداری سے اپنے احساسات بیان کیے، اور یہ ایک اہم قدم ہے۔"
            )
        elif risk_level == "high":
            intro_ur = (
                "آپ کے جوابات سے ظاہر ہوتا ہے کہ آپ حالیہ دنوں میں کافی دباؤ کا سامنا کر رہے ہیں۔ "
                "اپنے ساتھ نرمی سے پیش آئیں اور یاد رکھیں کہ مدد مانگنا بالکل درست ہے۔"
            )
        elif risk_level == "moderate":
            intro_ur = (
                "آپ کے جوابات سے معلوم ہوتا ہے کہ کچھ باتیں آپ کو پریشان کر رہی ہیں۔ "
                "اپنے احساسات کو سمجھنے اور اپنی دیکھ بھال کے لیے وقت نکالنا ضروری ہے۔"
            )
        else:
            intro_ur = (
                "اپنے احساسات کا جائزہ لینے کے لیے وقت نکالنے کا شکریہ۔ "
                "اپنی ذہنی صحت کا خیال رکھنا اہم ہے، چاہے حالات بظاہر ٹھیک ہی کیوں نہ ہوں۔"
            )

        # Urdu mood guidance
        if phq_sev == "Minimal":
            mood_ur = (
                "آپ کے ڈپریشن کے اسکور سے اس وقت شدید جذباتی پریشانی ظاہر نہیں ہوتی۔ "
                "اپنی نیند، روزمرہ معمولات اور اپنے قریبی لوگوں سے رابطے کا خیال رکھیں۔"
            )
        elif phq_sev == "Mild":
            mood_ur = (
                "آپ کو کچھ اداسی یا جذباتی بوجھ محسوس ہو سکتا ہے۔ "
                "اس کا مطلب یہ نہیں کہ آپ کمزور ہیں۔ اپنے احساسات کو اہمیت دیں اور اپنا خیال رکھیں۔"
            )
        elif phq_sev == "Moderate":
            mood_ur = (
                "آپ کے جوابات سے ظاہر ہوتا ہے کہ اداسی آپ کی روزمرہ زندگی کو متاثر کر سکتی ہے۔ "
                "اپنے آپ پر غیر ضروری دباؤ نہ ڈالیں اور ضرورت پڑنے پر کسی قابلِ اعتماد شخص سے بات کریں۔"
            )
        else:
            mood_ur = (
                "آپ کے جوابات سے شدید جذباتی پریشانی کا امکان ظاہر ہوتا ہے۔ "
                "اگر حالات ناقابلِ برداشت محسوس ہوں تو کسی قابلِ اعتماد شخص یا ذہنی صحت کے ماہر سے مدد لیں۔"
            )

        # Urdu anxiety guidance
        anxiety_ur = ""

        if gad_sev == "Mild":
            anxiety_ur = (
                "آپ کو کچھ پریشانی یا بے چینی بھی محسوس ہو سکتی ہے۔ "
                "آرام کے لیے وقفہ لیں اور اپنی نیند اور روزمرہ معمولات کا خیال رکھیں۔"
            )
        elif gad_sev == "Moderate":
            anxiety_ur = (
                "پریشانی اور مسلسل سوچ بچار آپ کے ذہن کو تھکا سکتی ہے۔ "
                "آہستہ سانس لینے کی مشق کریں اور خود کو پُرسکون ہونے کے لیے وقت دیں۔"
            )
        elif gad_sev == "Severe":
            anxiety_ur = (
                "آپ کے جوابات سے شدید بے چینی کا امکان ظاہر ہوتا ہے، جو نیند اور توجہ کو متاثر کر سکتی ہے۔ "
                "کسی ذہنی صحت کے ماہر یا ڈاکٹر سے بات کرنا مفید ہو سکتا ہے۔"
            )

        # Urdu wellbeing guidance
        if who_pct < 28:
            wellbeing_ur = (
                "آپ کی ذہنی خوشحالی کا اسکور کم ہے۔ اس وجہ سے تھکن، اداسی یا روزمرہ کاموں میں دلچسپی کی کمی محسوس ہو سکتی ہے۔ "
                "اپنی کیفیت کو نظرانداز نہ کریں اور ضرورت کے مطابق مدد حاصل کریں۔"
            )
        elif who_pct < 52:
            wellbeing_ur = (
                "آپ کی ذہنی خوشحالی کچھ کم دکھائی دیتی ہے۔ "
                "مناسب نیند، متوازن غذا، مختصر چہل قدمی اور کسی قریبی شخص سے بات کرنا مددگار ہو سکتا ہے۔"
            )
        else:
            wellbeing_ur = (
                "آپ کی ذہنی خوشحالی کا اسکور بہت کم نہیں ہے، جو ایک حوصلہ افزا بات ہے۔ "
                "اپنی مثبت عادات جاری رکھیں اور اپنی ضروریات کا خیال رکھیں۔"
            )

        # Urdu practical guidance based on risk
        if risk_level == "crisis":
            guidance_ur = (
                "اگر آپ کو خود کو نقصان پہنچانے کے خیالات آ رہے ہیں تو اکیلے نہ رہیں۔ "
                "کسی قابلِ اعتماد شخص کو فوراً بتائیں۔ فوری خطرے کی صورت میں 1122 پر رابطہ کریں۔ "
                "آپ اُمنگ سے 0311-7786264 پر بھی رابطہ کر سکتے ہیں۔"
            )
        elif risk_level == "high":
            guidance_ur = (
                "کسی قابلِ اعتماد شخص سے اپنے احساسات کے بارے میں بات کریں اور جلد کسی ذہنی صحت کے ماہر یا ڈاکٹر سے مشورہ لینے پر غور کریں۔ "
                "مدد لینے کے لیے حالات کے مزید خراب ہونے کا انتظار ضروری نہیں۔"
            )
        elif risk_level == "moderate":
            guidance_ur = (
                "اپنی دیکھ بھال کے لیے چھوٹے قدم اٹھائیں، مثلاً مناسب نیند، تازہ ہوا میں کچھ وقت گزارنا اور کسی قابلِ اعتماد شخص سے بات کرنا۔ "
                "ایک وقت میں سب کچھ کرنے کی کوشش نہ کریں۔"
            )
        else:
            guidance_ur = (
                "اپنے احساسات کو اہمیت دیتے رہیں۔ "
                "مستقل نیند، مناسب آرام اور قریبی لوگوں سے رابطہ آپ کی ذہنی صحت کا خیال رکھنے میں مدد دے سکتے ہیں۔"
            )

        parts_ur = [
            intro_ur,
            mood_ur,
            anxiety_ur,
            wellbeing_ur,
            guidance_ur
        ]

        if gad_total >= 5:
            parts_ur.append(
                "بے چینی محسوس ہونے پر آہستہ سانس لینے کی کوشش کریں: "
                "چار سیکنڈ میں سانس اندر لیں، چار سیکنڈ روکیں اور چھ سیکنڈ میں باہر نکالیں۔ "
                "اگر اس مشق سے تکلیف بڑھے تو معمول کے مطابق سانس لیں۔"
            )

        if who_pct < 52:
            parts_ur.append(
                "اپنے اوپر زیادہ بوجھ نہ ڈالیں۔ مختصر چہل قدمی، مناسب غذا، آرام یا کسی محفوظ اور قابلِ اعتماد شخص کے ساتھ کچھ وقت گزارنا بھی ایک اچھا قدم ہے۔"
            )

        # Compare with previous assessment, if available
        if prev and prev.get("phq9_total") is not None:
            chg = phq_total - prev["phq9_total"]

            if chg >= 5:
                parts_ur.append(
                    f"آپ کے پچھلے جائزے کے مقابلے میں ڈپریشن کا اسکور {chg} پوائنٹس بڑھا ہے۔ "
                    "اپنی کیفیت پر توجہ دیں اور ضرورت پڑنے پر پیشہ ورانہ مدد حاصل کریں۔"
                )
            elif chg <= -5:
                parts_ur.append(
                    f"آپ کے پچھلے جائزے کے مقابلے میں ڈپریشن کا اسکور {abs(chg)} پوائنٹس کم ہوا ہے۔ "
                    "یہ ایک مثبت پیش رفت ہے، اگرچہ اپنی دیکھ بھال جاری رکھنا اہم ہے۔"
                )

        parts_ur.append(
            "یاد رکھیں کہ آپ کے موجودہ احساسات اہم ہیں، لیکن یہ آپ کی پوری زندگی کی کہانی نہیں ہیں۔ "
            "مزید رہنمائی کے لیے Empath AI کی مرکزی چیٹ پر بات کریں۔ فوری خطرے کی صورت میں 1122 پر رابطہ کریں۔"
        )

        advice_text = "\n\n".join(
            p for p in parts_ur if p
        )

    else:
        advice_text = "\n\n".join(
            p for p in parts if p
        )

    return advice_text, risk_level

def save_assessment_json(session_data):
    try:
        record = {
            "resourceType": "MentalHealthAssessment",
            "patient_name": session_data.get("name", "Unknown"),
            "role": session_data.get("role", "Unknown"),
            "displacement_cause": session_data.get("displacement_cause", "Unknown"),
            "idp_hardships": session_data.get("idp_hardships", []),
            "assessment_date": datetime.now().isoformat(),
            "phq9_scores": dict(session_data.get("phq", {})),
            "gad7_scores": dict(session_data.get("gad", {})),
            "who5_scores": dict(session_data.get("who", {})),
            "phq9_total": sum(session_data.get("phq", {}).values()),
            "gad7_total": sum(session_data.get("gad", {}).values()),
            "who5_total": sum(session_data.get("who", {}).values()),
            "severity": session_data.get("severity", "Unknown"),
            "emotion_detected": session_data.get("emotion", "Unknown"),
            "flagged_for_followup": session_data.get("severity") in ["Moderate", "Severe"],
        }

        os.makedirs(os.path.join(BASE_PATH, "assessments"), exist_ok=True)
        fname = os.path.join(
            BASE_PATH,
            "assessments",
            f"{session_data.get('name', 'unknown')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)

    except Exception as e:
        print("Save JSON error:", e)

def ask_first_assessment_question(prev=None):
    session["stage"] = STAGE_ASSESS
    session["q_index"] = 0
    session["assessment_started"] = True
    session["phq"] = {}
    session["gad"] = {}
    session["who"] = {}

    if prev:
        intro = (
            "It has been a week since your last check-in. Let us see how you have been doing. "
            "Same questions — just answer as honestly as you can.\n\n"
        )
    else:
        intro = (
            "I want to understand how you have been feeling. I will ask you some questions — "
            "there are no right or wrong answers.\n\n"
        )
    return intro + SA_QUESTIONS[0]["text"] + "\n(0=Not at all  1=Several days  2=More than half the days  3=Nearly every day)"


SA_FOLLOWUP_QUESTIONS = {
    "crisis_risk": [
        "I saw from your recent self-assessment that you were going through something very serious. How have you been feeling since then? Are you safe right now?",
        "I wanted to check in — have you been able to reach out to anyone, or call Umang (0311-7786264) like I suggested?",
        "How is today feeling compared to when you did the assessment? I am here if something still feels very heavy.",
    ],
    "depression_high": [
        "I noticed from your recent self-assessment that low mood and energy have been affecting you. How have things been feeling since then?",
        "From your check-in, keeping a small daily routine was one of the suggestions. Have you been able to try anything small — even a short walk or a fixed prayer time?",
        "How is your mood this week compared to when you did the self-assessment? Has anything shifted, even a little?",
    ],
    "anxiety_high": [
        "Your recent self-assessment showed that worry has been quite high for you. What has been sitting most heavily on your mind lately?",
        "One of the suggestions from your check-in was the breathing technique — breathe in 4, hold 4, out 6. Have you had a chance to try it when worry builds up?",
        "How has the anxiety been this week compared to when you did the assessment? Is it feeling any different?",
    ],
    "wellbeing_low": [
        "Your self-assessment showed that your daily wellbeing has been very low. Have there been any small moments this week where things felt even slightly okay?",
        "From your check-in, one thing I suggested was identifying one small comfort — a familiar routine, a short rest, or a conversation with someone safe. Has anything like that helped?",
        "How are you feeling in yourself today? Any small change since you did the self-assessment?",
    ],
    "mild": [
        "You completed a self-assessment recently. How have things been feeling since then?",
        "From your check-in, things were manageable but still difficult. Has anything changed or felt heavier this week?",
        "How are you doing today compared to when you filled out the check-in?",
    ],
    "minimal": [
        "You completed a self-assessment recently. How have you been getting on since then?",
        "Your scores were in a reasonable range, but I know that does not always capture everything. Is there anything that has been on your mind this week?",
        "How are you feeling today? Anything you would like to talk through?",
    ],
}

SA_FOLLOWUP_QUESTIONS_UR = {
    "crisis_risk": [
        "آپ کے حالیہ جائزے سے معلوم ہوا تھا کہ آپ مشکل حالات سے گزر رہی تھیں۔ اس کے بعد سے آپ کیسا محسوس کر رہی ہیں؟ کیا آپ اس وقت محفوظ ہیں؟",
        "میں آپ کی خیریت معلوم کرنا چاہتی ہوں۔ کیا آپ نے کسی قابلِ اعتماد شخص سے بات کی ہے یا اُمنگ سے رابطہ کیا ہے؟",
        "جائزہ لینے کے وقت کے مقابلے میں آج آپ کیسا محسوس کر رہی ہیں؟ اگر اب بھی دل پر بوجھ ہے تو آپ مجھ سے بات کر سکتی ہیں۔",
    ],
    "depression_high": [
        "آپ کے حالیہ جائزے میں اداسی اور توانائی کی کمی سے متعلق علامات سامنے آئی تھیں۔ اس کے بعد سے آپ کیسا محسوس کر رہی ہیں؟",
        "ہم نے روزمرہ معمول میں چھوٹی چھوٹی تبدیلیاں لانے کی بات کی تھی۔ کیا آپ نے کوئی چھوٹا قدم اٹھایا، مثلاً تھوڑی دیر چہل قدمی یا آرام کے لیے وقت نکالنا؟",
        "اس ہفتے آپ کے مزاج میں پہلے کے مقابلے میں کوئی تبدیلی محسوس ہوئی ہے، چاہے وہ معمولی ہی کیوں نہ ہو؟",
    ],
    "anxiety_high": [
        "آپ کے حالیہ جائزے سے معلوم ہوا تھا کہ آپ کافی پریشانی محسوس کر رہی تھیں۔ آج کل کون سی بات آپ کے ذہن پر سب سے زیادہ بوجھ ڈال رہی ہے؟",
        "ہم نے پریشانی کے وقت آہستہ سانس لینے کی مشق پر بات کی تھی۔ کیا آپ نے اسے آزمایا؟",
        "جائزے کے وقت کے مقابلے میں اس ہفتے آپ کی بے چینی کیسی رہی ہے؟ کیا اس میں کوئی فرق محسوس ہوا؟",
    ],
    "wellbeing_low": [
        "آپ کے حالیہ جائزے میں ذہنی خوشحالی کا اسکور کم تھا۔ کیا اس ہفتے کوئی ایسا لمحہ آیا جب آپ کو ذرا سا سکون یا اچھا محسوس ہوا ہو؟",
        "ہم نے کسی ایسی چھوٹی چیز کو تلاش کرنے کی بات کی تھی جس سے آپ کو سکون ملے، مثلاً آرام، روزمرہ کا معمول یا کسی قابلِ اعتماد شخص سے بات کرنا۔ کیا کسی چیز سے مدد ملی؟",
        "آج آپ اندر سے کیسا محسوس کر رہی ہیں؟ کیا جائزے کے بعد کوئی چھوٹی سی تبدیلی آئی ہے؟",
    ],
    "mild": [
        "آپ نے حال ہی میں اپنی ذہنی صحت کا جائزہ لیا تھا۔ اس کے بعد سے آپ کیسا محسوس کر رہی ہیں؟",
        "آپ کے جوابات سے معلوم ہوا تھا کہ حالات کسی حد تک قابلِ برداشت ہیں، لیکن کچھ مشکلات اب بھی موجود ہیں۔ کیا اس ہفتے کوئی تبدیلی آئی ہے؟",
        "جس دن آپ نے جائزہ لیا تھا، اس دن کے مقابلے میں آج آپ کیسا محسوس کر رہی ہیں؟",
    ],
    "minimal": [
        "آپ نے حال ہی میں اپنی ذہنی صحت کا جائزہ لیا تھا۔ اس کے بعد سے آپ کے دن کیسے گزر رہے ہیں؟",
        "آپ کے اسکور مناسب حد میں تھے، لیکن اسکور ہمیشہ آپ کے تمام احساسات کی عکاسی نہیں کرتے۔ کیا اس ہفتے کوئی ایسی بات رہی جو آپ کے ذہن میں بار بار آئی ہو؟",
        "آج آپ کیسا محسوس کر رہی ہیں؟ کیا کوئی ایسی بات ہے جس کے بارے میں آپ بات کرنا چاہیں؟",
    ],
}


def load_recent_self_assessment(user_id):
    """Load the most recent self-assessment from DB into main chat session for follow-up."""
    last = get_last_assessment_for_user(user_id)
    if not last or last["phq9_total"] is None:
        return
    if days_since_assessment(last) >= 7:
        return

    phq_total = last["phq9_total"]
    gad_total = last["gad7_total"]
    who_total = last["who5_total"] or 0

    session["last_assessment_scores"] = {"phq9": phq_total, "gad7": gad_total, "who5": who_total}
    session["last_advice"] = last["advice_given"] or ""
    session["sa_followup_count"] = 0
    session["sa_followup_needed"] = True
    session["sa_listen_turns"] = 0

    # Determine dominant concern for targeted follow-up questions
    if phq_total >= 20 or gad_total >= 18:
        session["sa_dominant_concern"] = "crisis_risk"
    elif phq_total >= 10:
        session["sa_dominant_concern"] = "depression_high"
    elif gad_total >= 10:
        session["sa_dominant_concern"] = "anxiety_high"
    elif who_total * 4 < 40:
        session["sa_dominant_concern"] = "wellbeing_low"
    elif phq_total >= 5 or gad_total >= 5:
        session["sa_dominant_concern"] = "mild"
    else:
        session["sa_dominant_concern"] = "minimal"
    session["crisis_step"] = ""


def _check_and_start_assessment():
    """Returns response text. Enforces 7-day gate; loads prev assessment for comparison intro."""
    user_id = session.get("user_id")
    if user_id:
        last = get_last_assessment_for_user(user_id)
        days = days_since_assessment(last)
        if days < 7:
            next_date = (
                datetime.fromisoformat(str(last["assessment_date"])) + timedelta(days=7)
            ).strftime("%B %d")
            saved_advice = (last["advice_given"] or "").strip()
            result_text = (
                f"You have already completed your assessment this week. "
                f"Your next one will be available on {next_date}.\n\n"
                f"Saved results:\n"
                f"PHQ-9 (Depression): {score_to_10(last['phq9_total'], 27)}/10\n"
                f"GAD-7 (Anxiety): {score_to_10(last['gad7_total'], 21)}/10\n"
                f"WHO-5 (Wellbeing): {score_to_10(last['who5_total'], 25)}/10"
            )
            if saved_advice:
                result_text += f"\n\nSaved comments and guidance:\n{saved_advice}"
            result_text += "\n\nIf something feels urgent right now, I am here to talk."
            return result_text
        prev = last if (last and last["phq9_total"] is not None) else None
        return ask_first_assessment_question(prev=prev)
    return ask_first_assessment_question()



# ==============================
# ✅ SELF ASSESSMENT ROUTE (FIX 404)
# ==============================
@app.route("/self_assessment")
def self_assessment():
    return render_template("self_assessment.html")


# ─────────────────────────────────────────────
# SELF-ASSESSMENT CHAT (PHQ-9 + GAD-7 + WHO-5)
# ─────────────────────────────────────────────

SA_QUESTIONS = [
    # PHQ-9
    {"key": "phq1", "scale": "phq",
     "text": "Over the past one week — how often have you had little interest or pleasure in doing things?"},
    {"key": "phq2", "scale": "phq",
     "text": "How often have you been feeling down, depressed, or hopeless?"},
    {"key": "phq3", "scale": "phq",
     "text": "How often have you had trouble falling or staying asleep, or sleeping too much?"},
    {"key": "phq4", "scale": "phq",
     "text": "How often have you felt tired or had little energy?"},
    {"key": "phq5", "scale": "phq",
     "text": "How often have you had poor appetite or been overeating?"},
    {"key": "phq6", "scale": "phq",
     "text": "How often have you felt bad about yourself — or that you are a failure or have let yourself or your family down?"},
    {"key": "phq7", "scale": "phq",
     "text": "How often have you had trouble concentrating on things, such as reading or watching something?"},
    {"key": "phq8", "scale": "phq",
     "text": "How often have you been moving or speaking more slowly than usual, or been so restless that others have noticed?"},
    {"key": "phq9", "scale": "phq",
     "text": "How often have thoughts come up that you would be better off dead, or thoughts of hurting yourself in some way?"},

    # GAD-7
    {"key": "gad1", "scale": "gad",
     "text": "Now a few questions about worry and anxiety. How often have you felt nervous, anxious, or on edge?"},
    {"key": "gad2", "scale": "gad",
     "text": "How often have you been unable to stop or control worrying?"},
    {"key": "gad3", "scale": "gad",
     "text": "How often have you been worrying too much about different things?"},
    {"key": "gad4", "scale": "gad",
     "text": "How often have you had trouble relaxing?"},
    {"key": "gad5", "scale": "gad",
     "text": "How often have you been so restless that it was hard to sit still?"},
    {"key": "gad6", "scale": "gad",
     "text": "How often have you become easily annoyed or irritable?"},
    {"key": "gad7", "scale": "gad",
     "text": "How often have you felt afraid — as if something awful might happen?"},

    # WHO-5
    {"key": "who1", "scale": "who",
     "text": "Last few questions about wellbeing. Over the past two weeks, how often have you felt cheerful and in good spirits?"},
    {"key": "who2", "scale": "who",
     "text": "How often have you felt calm and relaxed?"},
    {"key": "who3", "scale": "who",
     "text": "How often have you felt active and full of energy?"},
    {"key": "who4", "scale": "who",
     "text": "How often did you wake up feeling fresh and rested?"},
    {"key": "who5", "scale": "who",
     "text": "How often has your daily life been filled with things that interest you?"},
]


# ─────────────────────────────────────────────
# URDU QUESTIONS
# ─────────────────────────────────────────────

SA_QUESTIONS_URDU = [
    # PHQ-9
    {"key": "phq1", "scale": "phq",
     "text": "گزشتہ ایک ہفتے کے دوران آپ کو کام کرنے میں دلچسپی یا خوشی کتنی بار کم محسوس ہوئی؟"},

    {"key": "phq2", "scale": "phq",
     "text": "آپ نے کتنی بار خود کو اداس، مایوس یا بے اُمید محسوس کیا؟"},

    {"key": "phq3", "scale": "phq",
     "text": "آپ کو کتنی بار سونے میں، نیند برقرار رکھنے میں، یا بہت زیادہ سونے میں دشواری ہوئی؟"},

    {"key": "phq4", "scale": "phq",
     "text": "آپ نے کتنی بار خود کو تھکا ہوا یا توانائی سے محروم محسوس کیا؟"},

    {"key": "phq5", "scale": "phq",
     "text": "آپ کو کتنی بار بھوک کم محسوس ہوئی یا آپ نے ضرورت سے زیادہ کھانا کھایا؟"},

    {"key": "phq6", "scale": "phq",
     "text": "آپ نے کتنی بار اپنے بارے میں برا محسوس کیا، یا یہ سوچا کہ آپ ناکام ہیں یا آپ نے خود کو یا اپنے خاندان کو مایوس کیا ہے؟"},

    {"key": "phq7", "scale": "phq",
     "text": "آپ کو کتنی بار چیزوں پر توجہ مرکوز کرنے میں دشواری ہوئی، جیسے پڑھنے یا کچھ دیکھنے میں؟"},

    {"key": "phq8", "scale": "phq",
     "text": "آپ کتنی بار معمول سے زیادہ آہستہ چلتے یا بولتے رہے، یا اتنے بے چین رہے کہ دوسروں نے بھی محسوس کیا؟"},

    {"key": "phq9", "scale": "phq",
     "text": "آپ کے ذہن میں کتنی بار یہ خیال آیا کہ آپ کا مر جانا بہتر ہوگا، یا آپ نے خود کو کسی طرح نقصان پہنچانے کے بارے میں سوچا؟"},

    # GAD-7
    {"key": "gad1", "scale": "gad",
     "text": "اب پریشانی اور بے چینی سے متعلق چند سوالات ہیں۔ آپ نے کتنی بار خود کو گھبرایا ہوا، بے چین یا پریشان محسوس کیا؟"},

    {"key": "gad2", "scale": "gad",
     "text": "آپ کتنی بار اپنی پریشانی کو روکنے یا قابو کرنے سے قاصر رہے؟"},

    {"key": "gad3", "scale": "gad",
     "text": "آپ نے کتنی بار مختلف چیزوں کے بارے میں بہت زیادہ فکر کی؟"},

    {"key": "gad4", "scale": "gad",
     "text": "آپ کو کتنی بار سکون کرنے یا آرام کرنے میں دشواری ہوئی؟"},

    {"key": "gad5", "scale": "gad",
     "text": "آپ کتنی بار اتنے بے چین رہے کہ ایک جگہ بیٹھنا مشکل محسوس ہوا؟"},

    {"key": "gad6", "scale": "gad",
     "text": "آپ کتنی بار آسانی سے چڑچڑے یا غصے میں آ گئے؟"},

    {"key": "gad7", "scale": "gad",
     "text": "آپ نے کتنی بار خوف محسوس کیا، جیسے کوئی بہت بری چیز ہونے والی ہو؟"},

    # WHO-5
    {"key": "who1", "scale": "who",
     "text": "اب آخری چند سوالات آپ کی ذہنی خوشحالی کے بارے میں ہیں۔ گزشتہ دو ہفتوں کے دوران آپ نے کتنی بار خود کو خوش اور اچھے موڈ میں محسوس کیا؟"},

    {"key": "who2", "scale": "who",
     "text": "آپ نے کتنی بار خود کو پرسکون اور مطمئن محسوس کیا؟"},

    {"key": "who3", "scale": "who",
     "text": "آپ نے کتنی بار خود کو فعال اور توانائی سے بھرپور محسوس کیا؟"},

    {"key": "who4", "scale": "who",
     "text": "آپ کتنی بار صبح تازہ دم اور آرام محسوس کرتے ہوئے بیدار ہوئے؟"},

    {"key": "who5", "scale": "who",
     "text": "آپ کی روزمرہ زندگی کتنی بار ایسی چیزوں سے بھری رہی جن میں آپ کی دلچسپی تھی؟"},
]


# ─────────────────────────────────────────────
# OPTIONS
# ─────────────────────────────────────────────

PHQ_GAD_OPTIONS = [
    {"label": "0 — Not at all", "value": "0"},
    {"label": "1 — Several days", "value": "1"},
    {"label": "2 — More than half the days", "value": "2"},
    {"label": "3 — Nearly every day", "value": "3"},
]

PHQ_GAD_OPTIONS_URDU = [
    {"label": "0 — بالکل نہیں", "value": "0"},
    {"label": "1 — کئی دن", "value": "1"},
    {"label": "2 — آدھے سے زیادہ دن", "value": "2"},
    {"label": "3 — تقریباً ہر روز", "value": "3"},
]


WHO_OPTIONS = [
    {"label": "0 — At no time", "value": "0"},
    {"label": "1 — Some of the time", "value": "1"},
    {"label": "2 — Less than half the time", "value": "2"},
    {"label": "3 — More than half the time", "value": "3"},
    {"label": "4 — All of the time", "value": "4"},
]

WHO_OPTIONS_URDU = [
    {"label": "0 — بالکل بھی نہیں", "value": "0"},
    {"label": "1 — کچھ وقت", "value": "1"},
    {"label": "2 — آدھے سے کم وقت", "value": "2"},
    {"label": "3 — آدھے سے زیادہ وقت", "value": "3"},
    {"label": "4 — ہر وقت", "value": "4"},
]


# ─────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────

def score_to_10(raw, max_raw):
    """Normalise a raw score to a 0-10 scale, rounded to 1 decimal."""
    return round(raw / max_raw * 10, 1)


def sa_get_phq9_severity(score):
    if score <= 4:
        return ("Minimal", "Your score suggests minimal depression symptoms right now.")
    if score <= 9:
        return ("Mild", "Your score suggests mild depression. These feelings are real and worth addressing.")
    if score <= 14:
        return ("Moderate", "Your score suggests moderate depression. Speaking with a counselor is strongly recommended.")
    if score <= 19:
        return ("Moderately Severe", "Your score suggests moderately severe depression. Please seek professional support soon.")
    return ("Severe", "Your score suggests severe depression. Please reach out for professional help immediately.")


def sa_get_gad7_severity(score):
    if score <= 4:
        return ("Minimal", "Your anxiety levels appear minimal right now.")
    if score <= 9:
        return ("Mild", "Your score suggests mild anxiety. Managing stress and rest can help.")
    if score <= 14:
        return ("Moderate", "Your score suggests moderate anxiety. Talking to someone can make a real difference.")
    return ("Severe", "Your score suggests severe anxiety. Professional support is strongly recommended.")


def sa_get_who5_interpretation(score):
    pct = score * 4

    if pct >= 72:
        return "Your wellbeing score is good. You are showing positive signs of mental wellness."

    if pct >= 52:
        return "Your wellbeing score is moderate. There may be room to improve your daily sense of ease and energy."

    if pct >= 28:
        return "Your wellbeing score is low. This may suggest emotional fatigue or risk of depression — support is available."

    return "Your wellbeing score is very low. This strongly suggests a need for professional mental health support."



def sa_build_result(phq_scores, gad_scores, who_scores):

    phq_total = sum(phq_scores.values())
    gad_total = sum(gad_scores.values())
    who_total = sum(who_scores.values())

    phq_q9 = phq_scores.get("phq9", 0)

    # Get selected language
    language = session.get(
        "sa_language",
        session.get("language", "en")
    )

    # Get original severity labels
    phq_sev = sa_get_phq9_severity(phq_total)[0]
    gad_sev = sa_get_gad7_severity(gad_total)[0]

    crisis_flag = phq_q9 >= 2

    # Translate severity labels only for Urdu
    if language == "ur":
        severity_urdu = {
            "Minimal": "نہ ہونے کے برابر",
            "Mild": "ہلکی",
            "Moderate": "درمیانی",
            "Moderately Severe": "درمیانے سے زیادہ شدید",
            "Severe": "شدید"
        }

        phq_sev = severity_urdu.get(phq_sev, phq_sev)
        gad_sev = severity_urdu.get(gad_sev, gad_sev)

    if language == "ur":

        lines = [
            "یہ assessment مکمل کرنے کا شکریہ۔ آپ کے نتائج کا خلاصہ یہ ہے:\n",

            f"PHQ-9 (ڈپریشن) — اسکور: "
            f"{score_to_10(phq_total, 27)}/10 — {phq_sev}",

            f"GAD-7 (بے چینی) — اسکور: "
            f"{score_to_10(gad_total, 21)}/10 — {gad_sev}",

            f"WHO-5 (ذہنی خوشحالی) — اسکور: "
            f"{score_to_10(who_total, 25)}/10",
        ]

        advice, risk_level = generate_assessment_advice(
            phq_total,
            gad_total,
            who_total,
            phq_q9
        )

        if advice:
            lines.append(
                f"\n── ذاتی رہنمائی ──\n\n{advice}"
            )

        if risk_level == "crisis" or crisis_flag:
            lines.append(
                "\nفوری اور مسلسل مدد کے لیے Main Chat کھولیں۔ "
                "Empath AI آپ کے نتائج کے مطابق آپ کی رہنمائی کرے گا۔ "
                "[Main Chat پر جائیں →](/)"
            )

        elif risk_level in ("high", "moderate"):
            lines.append(
                "\nمزید ذاتی رہنمائی اور اپنی progress کو track کرنے کے لیے "
                "Main Chat پر جائیں، جہاں Empath AI آپ سے براہ راست بات کر سکتا ہے۔ "
                "[Main Chat پر جائیں →](/)"
            )

        else:
            lines.append(
                "\nاگر آپ کو کچھ پریشان کر رہا ہو یا آپ اپنے احساسات کے بارے میں بات کرنا چاہیں "
                "تو Main Chat ہمیشہ آپ کے لیے موجود ہے۔ "
                "[Main Chat پر جائیں →](/)"
            )

    else:

        lines = [
            "Thank you for completing this assessment. Here is a summary of your results:\n",

            f"PHQ-9 (Depression) — Score: "
            f"{score_to_10(phq_total, 27)}/10 — {phq_sev}",

            f"GAD-7 (Anxiety) — Score: "
            f"{score_to_10(gad_total, 21)}/10 — {gad_sev}",

            f"WHO-5 (Wellbeing) — Score: "
            f"{score_to_10(who_total, 25)}/10",
        ]

        advice, risk_level = generate_assessment_advice(
            phq_total,
            gad_total,
            who_total,
            phq_q9
        )

        if advice:
            lines.append(
                f"\n── Personal Guidance ──\n\n{advice}"
            )

        if risk_level == "crisis" or crisis_flag:
            lines.append(
                "\nFor immediate, ongoing support please open the main chat — "
                "Empath AI will follow up on your results and guide you step by step. "
                "[Go to Main Chat →](/)"
            )

        elif risk_level in ("high", "moderate"):
            lines.append(
                "\nFor more personalised guidance and to keep track of your progress over time, "
                "visit the main chat where Empath AI can talk with you directly. "
                "[Go to Main Chat →](/)"
            )

        else:
            lines.append(
                "\nIf anything feels heavy or you want to talk through what you are feeling, "
                "the main chat is always open for you. "
                "[Go to Main Chat →](/)"
            )

    return "\n".join(lines), crisis_flag



# ─────────────────────────────────────────────
# SESSION
# ─────────────────────────────────────────────

def sa_init_session():

    session["sa_q_index"] = 0
    session["sa_phq"] = {}
    session["sa_gad"] = {}
    session["sa_who"] = {}
    session["sa_done"] = False
    session["sa_awaiting_name"] = session.get("user_id") is None
    session["sa_user_id"] = session.get("user_id")

    # Keep selected language
    if "language" not in session:
        session["language"] = "en"


# ─────────────────────────────────────────────
# ALREADY COMPLETED RESPONSE
# ─────────────────────────────────────────────


def _sa_already_done_response(last_row, name=""):

    language = session.get(
        "sa_language",
        session.get("language", "en")
    )

    next_date = (
        datetime.fromisoformat(
            str(last_row["assessment_date"])
        ) + timedelta(days=7)
    ).strftime("%B %d")

    if language == "ur":

        greeting = f"السلام علیکم {name}۔ " if name else ""

        response_text = (
            f"{greeting}"
            f"آپ نے اپنا ہفتہ وار assessment پہلے ہی مکمل کر لیا ہے۔ "
            f"آپ کا اگلا assessment {next_date} کو دستیاب ہوگا۔\n\n"
            f"محفوظ شدہ اسکور:\n"
            f"PHQ-9 (ڈپریشن): "
            f"{score_to_10(last_row['phq9_total'], 27)}/10\n"
            f"GAD-7 (بے چینی): "
            f"{score_to_10(last_row['gad7_total'], 21)}/10\n"
            f"WHO-5 (ذہنی خوشحالی): "
            f"{score_to_10(last_row['who5_total'], 25)}/10"
        )

        saved_advice = (
            last_row["advice_given"] or ""
        ).strip()

        if saved_advice:
            # Translate previously saved English guidance for Urdu display
            try:
                translation = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Translate the complete mental-health "
                                "assessment guidance into natural Pakistani "
                                "Urdu written in Urdu script. Translate every "
                                "English sentence. Do not use Roman Urdu. "
                                "Preserve meaning, scores, phone numbers, "
                                "safety advice and numerical values. "
                                "Return only the Urdu translation."
                            )
                        },
                        {
                            "role": "user",
                            "content": saved_advice
                        }
                    ],
                    temperature=0
                )

                translated_advice = (
                    translation.choices[0].message.content or ""
                ).strip()

                if translated_advice:
                    saved_advice = translated_advice

            except Exception as e:
                print(
                    "Saved assessment Urdu translation error:",
                    repr(e)
                )

            response_text += (
                f"\n\nمحفوظ شدہ تبصرے اور رہنمائی:\n"
                f"{saved_advice}"
            )

        response_text += (
            "\n\nاگر کوئی صورتحال فوری ہو تو Main Chat پر واپس جائیں "
            "یا Umang: 0311-7786264 پر رابطہ کریں۔"
        )

    else:

        greeting = f"Hi {name}. " if name else ""

        response_text = (
            f"{greeting}"
            f"You have already completed your weekly assessment. "
            f"Your next one will be available on {next_date}.\n\n"
            f"Saved scores:\n"
            f"PHQ-9 (Depression): "
            f"{score_to_10(last_row['phq9_total'], 27)}/10\n"
            f"GAD-7 (Anxiety): "
            f"{score_to_10(last_row['gad7_total'], 21)}/10\n"
            f"WHO-5 (Wellbeing): "
            f"{score_to_10(last_row['who5_total'], 25)}/10"
        )

        saved_advice = (
            last_row["advice_given"] or ""
        ).strip()

        if saved_advice:
            response_text += (
                f"\n\nSaved comments and guidance:\n"
                f"{saved_advice}"
            )

        response_text += (
            "\n\nIf something feels urgent, please return to the main chat "
            "or call Umang: 0311-7786264."
        )

    return jsonify({
        "response": response_text,
        "options": [],
        "type": "already_done",
    })

# ─────────────────────────────────────────────
# START QUESTIONS
# ─────────────────────────────────────────────

def _sa_start_questions(name=""):

    language = session.get("language", "en")

    if language == "ur":

        first_q = SA_QUESTIONS_URDU[0]

        greeting = f"السلام علیکم {name}۔ " if name else "السلام علیکم۔ "

        response_text = (
            greeting +
            "میں آپ سے ڈپریشن، بے چینی اور ذہنی خوشحالی کے بارے میں "
            "21 مختصر سوالات پوچھوں گا۔ "
            "اس میں کوئی صحیح یا غلط جواب نہیں ہے — بس ایمانداری سے جواب دیں۔\n\n" +
            first_q["text"]
        )

        return jsonify({
            "response": response_text,
            "options": PHQ_GAD_OPTIONS_URDU,
        })

    else:

        first_q = SA_QUESTIONS[0]

        greeting = f"Hello{', ' + name if name else ''}. "

        return jsonify({
            "response": (
                greeting +
                "I will ask you 21 short questions covering depression, anxiety, and wellbeing. "
                "There are no right or wrong answers — just answer as honestly as you can.\n\n" +
                first_q["text"]
            ),
            "options": PHQ_GAD_OPTIONS,
        })


# ─────────────────────────────────────────────
# INIT
# ─────────────────────────────────────────────

@app.route("/assessment_chat_init", methods=["GET"])
def assessment_chat_init():

    # Get language from frontend
    language = request.args.get("language", "en")

    if language not in ["en", "ur"]:
        language = "en"

    # Save selected language
    session["language"] = language
    session["sa_language"] = language

    # -----------------------------------------
    # GET LOGGED-IN USER AUTOMATICALLY
    # -----------------------------------------

    if not session.get("user_id") and session.get("account_id"):

        linked_user = get_user_by_account_id(
            session.get("account_id")
        )

        if not linked_user:
            linked_user = get_or_create_user_for_account(
                session.get("account_id"),
                session.get("account_name", "User")
            )

        if linked_user:
            session["user_id"] = linked_user["id"]
            session["name"] = linked_user["name"]

    # Initialize assessment AFTER user is found
    sa_init_session()

    user_id = session.get("user_id")

    # -----------------------------------------
    # LOGGED-IN USER
    # -----------------------------------------

    if user_id:

        last = get_last_assessment_for_user(user_id)

        # Assessment already completed within 7 days
        if days_since_assessment(last) < 7:

            session["sa_done"] = True

            user = get_user_by_id(user_id)

            return _sa_already_done_response(
                last,
                user["name"] if user else ""
            )

        user = get_user_by_id(user_id)

        session["sa_awaiting_name"] = False
        session["sa_user_id"] = user_id

        # Use Urdu name when Urdu is selected
        if language == "ur":

            display_name = get_urdu_name_for_account(
                session.get("account_id")
            )

            if not display_name:
                display_name = user["name"] if user else ""

        else:

            display_name = user["name"] if user else ""

        return _sa_start_questions(display_name)

    # -----------------------------------------
    # NO LOGIN
    # -----------------------------------------

    if language == "ur":

        return jsonify({
            "response": "اپنے ہفتہ وار خود جائزے میں خوش آمدید۔ شروع کرنے سے پہلے، آپ کا نام کیا ہے؟",
            "options": [],
        })

    return jsonify({
        "response": "Welcome to your weekly self-assessment. Before we begin, what is your name?",
        "options": [],
    })


# ─────────────────────────────────────────────
# ASSESSMENT CHAT
# ─────────────────────────────────────────────

@app.route("/assessment_chat", methods=["POST"])
def assessment_chat():

    data = request.json or {}

    user_message = data.get("message", "").strip()

    # Get language from frontend/session
    language = data.get(
        "language",
        session.get("language", "en")
    )

    if language not in ["en", "ur"]:
        language = "en"

    session["language"] = language
    session["sa_language"] = language

    # Empty message
    if not user_message:

        if language == "ur":
            return jsonify({
                "response": "مجھے یہ سمجھ نہیں آیا۔ براہ کرم نیچے دیے گئے options میں سے ایک منتخب کریں یا نمبر لکھیں۔",
                "options": PHQ_GAD_OPTIONS_URDU
            })

        return jsonify({
            "response": "I did not catch that. Please choose an option or type a number.",
            "options": []
        })

    # Already completed
    if session.get("sa_done"):

        if language == "ur":
            return jsonify({
                "response": "آپ یہ assessment پہلے ہی مکمل کر چکے ہیں۔ دوبارہ شروع کرنے کے لیے Restart دبائیں۔",
                "options": []
            })

        return jsonify({
            "response": "You have already completed this assessment. Press Restart to begin again.",
            "options": []
        })


    # ─────────────────────────────────────────────
    # NAME STEP
    # ─────────────────────────────────────────────

    if session.get("sa_awaiting_name"):

        raw_name = user_message.strip().split()[0].capitalize()

        if len(raw_name) < 2:

            if language == "ur":
                return jsonify({
                    "response": "براہ کرم جاری رکھنے کے لیے اپنا نام درج کریں۔",
                    "options": []
                })

            return jsonify({
                "response": "Please enter your name to continue.",
                "options": []
            })

        existing = find_existing_user(raw_name)

        if existing:

            sa_uid = existing["id"]

            update_user_last_seen(sa_uid)

        else:

            sa_uid = create_user(raw_name)

        session["sa_user_id"] = sa_uid
        session["sa_awaiting_name"] = False

        last = get_last_assessment_for_user(sa_uid)

        if days_since_assessment(last) < 7:

            session["sa_done"] = True

            return _sa_already_done_response(
                last,
                raw_name
            )

        return _sa_start_questions(raw_name)


    # ─────────────────────────────────────────────
    # QUESTION ANSWERING
    # ─────────────────────────────────────────────

    q_index = session.get("sa_q_index", 0)

    number_match = re.search(
        r"\b([0-4])\b",
        user_message
    )

    score = (
        int(number_match.group(1))
        if number_match
        else None
    )


    # Invalid answer
    if score is None:

        current_q = (
            SA_QUESTIONS[q_index]
            if q_index < len(SA_QUESTIONS)
            else None
        )

        if language == "ur":

            options = (
                WHO_OPTIONS_URDU
                if (
                    current_q
                    and current_q["scale"] == "who"
                )
                else PHQ_GAD_OPTIONS_URDU
            )

            return jsonify({
                "response": "براہ کرم نیچے دیے گئے options میں سے ایک منتخب کریں یا نمبر لکھیں۔",
                "options": options
            })

        options = (
            WHO_OPTIONS
            if (
                current_q
                and current_q["scale"] == "who"
            )
            else PHQ_GAD_OPTIONS
        )

        return jsonify({
            "response": "Please choose one of the options below, or type a number.",
            "options": options
        })


    # ─────────────────────────────────────────────
    # SAVE ANSWER
    # ─────────────────────────────────────────────

    if q_index < len(SA_QUESTIONS):

        prev_q = SA_QUESTIONS[q_index]

        clamped = min(
            max(score, 0),
            4 if prev_q["scale"] == "who" else 3
        )


        # PHQ
        if prev_q["scale"] == "phq":

            phq = dict(
                session.get("sa_phq", {})
            )

            phq[prev_q["key"]] = clamped

            session["sa_phq"] = phq


            # PHQ-9 crisis question
            if (
                prev_q["key"] == "phq9"
                and clamped >= 2
            ):

                session["sa_q_index"] = q_index + 1

                if language == "ur":

                    return jsonify({
                        "response": (
                            "میں یہاں ایک لمحے کے لیے رکنا چاہتا ہوں۔ "
                            "آپ کے جواب سے لگتا ہے کہ آپ اس وقت بہت مشکل صورتحال سے گزر رہے ہیں۔ "
                            "براہ کرم یاد رکھیں کہ یہ کیفیت ہمیشہ ایک جیسی نہیں رہتی۔ "
                            "آپ اس وقت مدد کے مستحق ہیں۔ "
                            "براہ کرم Umang: 0311-7786264 پر رابطہ کریں، "
                            "کسی قابلِ اعتماد شخص سے بات کریں، "
                            "اور جب آپ خود کو کچھ زیادہ محفوظ محسوس کریں تو واپس آئیں۔"
                        ),
                        "options": [],
                        "type": "crisis",
                    })

                return jsonify({
                    "response": (
                        "I want to pause here for a moment. "
                        "Your answer suggests you may be going through something very heavy. "
                        "Please know this can be a phase, and life does not stay the same forever. "
                        "You deserve support right now — please call Umang: 0311-7786264, "
                        "reach out to someone you trust, and come back only when you feel a little safer."
                    ),
                    "options": [],
                    "type": "crisis",
                })


        # GAD
        elif prev_q["scale"] == "gad":

            gad = dict(
                session.get("sa_gad", {})
            )

            gad[prev_q["key"]] = clamped

            session["sa_gad"] = gad


        # WHO
        elif prev_q["scale"] == "who":

            who = dict(
                session.get("sa_who", {})
            )

            who[prev_q["key"]] = clamped

            session["sa_who"] = who


        session["sa_q_index"] = q_index + 1

        q_index += 1


    # ─────────────────────────────────────────────
    # ALL QUESTIONS COMPLETED
    # ─────────────────────────────────────────────

    if q_index >= len(SA_QUESTIONS):

        session["sa_done"] = True

        phq_scores = session.get(
            "sa_phq",
            {}
        )

        gad_scores = session.get(
            "sa_gad",
            {}
        )

        who_scores = session.get(
            "sa_who",
            {}
        )

        result_text, crisis = sa_build_result(
            phq_scores,
            gad_scores,
            who_scores
        )

        sa_uid = (
            session.get("sa_user_id")
            or session.get("user_id")
        )

        phq_total = sum(
            phq_scores.values()
        )

        gad_total = sum(
            gad_scores.values()
        )

        who_total = sum(
            who_scores.values()
        )

        phq_q9 = phq_scores.get(
            "phq9",
            0
        )

        prev_assess = (
            get_last_assessment_for_user(sa_uid)
            if sa_uid
            else None
        )

        if (
            prev_assess
            and prev_assess["phq9_total"] is None
        ):
            prev_assess = None


        advice_text, risk_level = generate_assessment_advice(
            phq_total,
            gad_total,
            who_total,
            phq_q9,
            prev_assess
        )

        session["last_advice"] = advice_text

        session["last_assessment_scores"] = {
            "phq9": phq_total,
            "gad7": gad_total,
            "who5": who_total
        }

        session["sa_followup_count"] = 0
        session["sa_followup_needed"] = True
        session["sa_listen_turns"] = 0


        if phq_total >= 20 or gad_total >= 18:

            session["sa_dominant_concern"] = "crisis_risk"

        elif phq_total >= 10:

            session["sa_dominant_concern"] = "depression_high"

        elif gad_total >= 10:

            session["sa_dominant_concern"] = "anxiety_high"

        elif who_total * 4 < 40:

            session["sa_dominant_concern"] = "wellbeing_low"

        elif phq_total >= 5 or gad_total >= 5:

            session["sa_dominant_concern"] = "mild"

        else:

            session["sa_dominant_concern"] = "minimal"


        # ─────────────────────────────────────────────
        # SAVE TO DATABASE
        # ─────────────────────────────────────────────

        if sa_uid:

            try:

                db = get_db()

                db.execute("""
                    INSERT INTO assessments (
                        user_id,
                        phq9_total,
                        gad7_total,
                        who5_total,
                        severity,
                        emotion_detected,
                        displacement_cause,
                        idp_hardships,
                        advice_given,
                        prev_phq9,
                        prev_gad7,
                        prev_who5
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (

                    sa_uid,

                    phq_total,

                    gad_total,

                    who_total,

                    sa_get_phq9_severity(
                        phq_total
                    )[0],

                    session.get(
                        "emotion",
                        "Unknown"
                    ),

                    session.get(
                        "displacement_cause",
                        "Unknown"
                    ),

                    json.dumps([]),

                    advice_text,

                    prev_assess["phq9_total"]
                    if prev_assess
                    else None,

                    prev_assess["gad7_total"]
                    if prev_assess
                    else None,

                    prev_assess["who5_total"]
                    if prev_assess
                    else None,

                ))

                db.commit()

            except Exception as e:

                print(
                    "SA DB save error:",
                    e
                )


        return jsonify({
            "response": result_text,
            "options": [],
            "type": (
                "crisis"
                if (
                    crisis
                    or risk_level == "crisis"
                )
                else "result"
            )
        })


    # ─────────────────────────────────────────────
    # NEXT QUESTION
    # ─────────────────────────────────────────────

    if language == "ur":

        next_q = SA_QUESTIONS_URDU[q_index]

        options = (
            WHO_OPTIONS_URDU
            if next_q["scale"] == "who"
            else PHQ_GAD_OPTIONS_URDU
        )

        return jsonify({
            "response": next_q["text"],
            "options": options
        })

    else:

        next_q = SA_QUESTIONS[q_index]

        options = (
            WHO_OPTIONS
            if next_q["scale"] == "who"
            else PHQ_GAD_OPTIONS
        )

        return jsonify({
            "response": next_q["text"],
            "options": options
        })


# ─────────────────────────────────────────────
# RESET
# ─────────────────────────────────────────────

@app.route("/assessment_chat_reset", methods=["POST"])
def assessment_chat_reset():

    # Keep selected language during restart
    language = session.get("language", "en")

    sa_init_session()

    session["language"] = language

    return jsonify({
        "status": "reset"
    })


# ─────────────────────────────────────────────
# Names used inside functions, imported at the end of the file so the
# modules can use each other without circular-import problems.
# ─────────────────────────────────────────────
from .context import (  # noqa: E402
    STAGE_ASSESS,
)
from .core import (  # noqa: E402
    BASE_PATH,
    client,
    f,
)
from .database import (  # noqa: E402
    create_user,
    days_since_assessment,
    find_existing_user,
    get_db,
    get_last_assessment_for_user,
    get_or_create_user_for_account,
    get_urdu_name_for_account,
    get_user_by_account_id,
    get_user_by_id,
    update_user_last_seen,
)

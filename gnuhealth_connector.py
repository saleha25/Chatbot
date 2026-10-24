import requests
import uuid
import os


THALAMUS_URL = "http://127.0.0.1:5001"

GNUHEALTH_PERSON_ID = os.getenv("GNUHEALTH_PERSON_ID")
GNUHEALTH_USERNAME = os.getenv("GNUHEALTH_USERNAME")
GNUHEALTH_PASSWORD = os.getenv("GNUHEALTH_PASSWORD")

def create_gnuhealth_account(person_id, password):
    """
    Register a new Empath AI user with GNU Health Federation.
    """
    
    import subprocess

    try:
        result = subprocess.run(
            [
                "wsl.exe",
                "bash",
                "-lc",
                "cat ~/.config/empath-ai/onboarding_secret"
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True
        )
        onboarding_secret = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {
            "success": False,
            "status_code": None,
            "response": "Could not read the onboarding secret from WSL."
        }


    if not onboarding_secret:
        return {
            "success": False,
            "status_code": None,
            "response": "Onboarding secret is empty."
        }

    try:
        response = requests.post(
            f"{THALAMUS_URL}/empath/onboard",
            json={
                "id": person_id,
                "password": password
            },
            headers={
                "X-Onboarding-Secret": onboarding_secret
            },
            timeout=10
        )

        return {
            "success": response.status_code == 201,
            "status_code": response.status_code,
            "response": response.text
        }

    except requests.RequestException as e:
        return {
            "success": False,
            "status_code": None,
            "response": str(e)
        }


def send_wellness_to_gnuhealth(
    person_id,
    username=None,
    password=None,
    sleep_hours=None,
    mood=None,
    energy=None
):
    """Send native MyGNUHealth wellness data through the protected endpoint."""
    import subprocess

    try:
        result = subprocess.run(
            [
                "wsl.exe",
                "bash",
                "-lc",
                "cat ~/.config/empath-ai/onboarding_secret"
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True
        )
        onboarding_secret = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {
            "success": False,
            "status_code": None,
            "response": "Could not read the onboarding secret from WSL."
        }

    if not onboarding_secret:
        return {
            "success": False,
            "status_code": None,
            "response": "The onboarding secret is empty."
        }

    page_id = f"wellness_{uuid.uuid4().hex[:12]}"
    measurements = []

    if mood is not None or energy is not None:
        measurements.append({
            "mood_energy": {"mood": mood, "energy": energy}
        })

    if sleep_hours is not None:
        measurements.append({
            "sleep": {
                "sleeptime": sleep_hours,
                "sleepquality": "good" if sleep_hours >= 6 else "low"
            }
        })

    data = {
        "id": page_id,
        "page": page_id,
        "page_date": __import__("datetime").datetime.now().isoformat(),
        "book": person_id,
        "domain": "medical",
        "context": "self_monitoring",
        "measurements": measurements,
        "summary": "",
        "info": "Empath AI wellness"
    }

    try:
        response = requests.post(
            f"{THALAMUS_URL}/empath/wellness",
            json={
                "person_id": person_id,
                "page": data
            },
            headers={"X-Onboarding-Secret": onboarding_secret},
            timeout=10
        )

        return {
            "success": response.status_code == 201,
            "status_code": response.status_code,
            "response": response.text
        }
    except requests.RequestException as e:
        return {
            "success": False,
            "status_code": None,
            "response": str(e)
        }
def update_mygnuhealth_tracker(
    profile,
    sleep_hours=None,
    mood=None,
    energy=None
):
    """
    Update MyGNUHealth heart trackers and Book of Life
    for a specific user profile.
    """

    import subprocess

    script = """
from mygnuhealth.core import maindb, boldb
from datetime import datetime
import uuid
import sys

sleep_hours = None if sys.argv[1] == "None" else float(sys.argv[1])
mood = None if sys.argv[2] == "None" else sys.argv[2]
energy = None if sys.argv[3] == "None" else sys.argv[3]

measurements = []

# Update Mood/Energy heart tracker
if mood is not None or energy is not None:
    maindb.table('mood').insert({
        'timestamp': datetime.now().isoformat(),
        'event_id': str(uuid.uuid4()),
        'synced': False,
        'mood': mood,
        'energy': energy
    })

    measurements.append({
        'mood_energy': {
            'mood': mood,
            'energy': energy
        }
    })

# Update Sleep heart tracker
if sleep_hours is not None:

    sleep_quality = 'good' if sleep_hours >= 6 else 'poor'

    maindb.table('sleep').insert({
        'timestamp': datetime.now().isoformat(),
        'event_id': str(uuid.uuid4()),
        'synced': False,
        'sleeptime': sleep_hours,
        'sleepquality': sleep_quality
    })

    measurements.append({
        'sleep': {
            'sleeptime': sleep_hours,
            'sleepquality': sleep_quality
        }
    })

# Create Book of Life record
if measurements:

    page_id = 'wellness_' + uuid.uuid4().hex[:12]

    boldb.table('pol').insert({
        'book': 'empath_' + sys.argv[4],
        'page_date': datetime.now().isoformat(),
        'age': None,
        'domain': 'medical',
        'relevance': None,
        'privacy': None,
        'context': 'self_monitoring',
        'measurements': measurements,
        'genetic_info': '',
        'summary': '',
        'info': 'Empath AI wellness',
        'node': 'mygnuhealth',
        'author': 'empath_' + sys.argv[4],
        'author_acct': 'empath_' + sys.argv[4],
        'fsynced': True,
        'page': page_id
    })

    print('Book of Life record created:', page_id)

print('MyGNUHealth tracker and Book of Life updated successfully')
"""

    profile_paths = {
        "empath_izak": "/home/usman/mygnuhealth_profiles/izak",
       "empath_saleha": "/home/usman/mygnuhealth_profiles/saleha"
    }

    if profile.lower() not in profile_paths:
        print("Unknown MyGNUHealth profile:", profile)
        return False

    xdg_config = profile_paths[profile.lower()]
    short_profile = profile.lower().replace("empath_", "")

    try:
        result = subprocess.run(
            [
                "wsl",
                "-d",
                "Ubuntu-24.04",
                "--",
                "env",
                f"XDG_CONFIG_HOME={xdg_config}",
                "/home/usman/.local/share/pipx/venvs/pip/bin/python",
                "-c",
                script,
                str(sleep_hours),
                str(mood),
                str(energy),
                short_profile
            ],
            capture_output=True,
            text=True,
            timeout=30
        )

        print(result.stdout)

        if result.returncode != 0:
            print("MyGNUHealth update failed:")
            print(result.stderr)
            return False

        return True

    except Exception as e:
        print("MyGNUHealth update error:", e)
        return False
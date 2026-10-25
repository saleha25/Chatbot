import os
import re
import uuid
import requests
import subprocess
from datetime import datetime


THALAMUS_URL = os.getenv("THALAMUS_URL", "http://127.0.0.1:5001")
GNUHEALTH_PERSON_ID = os.getenv("GNUHEALTH_PERSON_ID", "")
GNUHEALTH_USERNAME = os.getenv("GNUHEALTH_USERNAME", "")
GNUHEALTH_PASSWORD = os.getenv("GNUHEALTH_PASSWORD", "")


def create_gnuhealth_account(person_id, password):
    """Register a new user with GNU Health Federation."""

    try:
        result = subprocess.run(
            [
                "wsl.exe",
                "bash",
                "-lc",
                "cat ~/.config/empath-ai/onboarding_secret",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        onboarding_secret = result.stdout.strip()

        if not onboarding_secret:
            return {
                "success": False,
                "status_code": None,
                "response": "Onboarding secret is empty.",
            }

        response = requests.post(
            f"{THALAMUS_URL}/empath/onboard",
            json={"id": person_id, "password": password},
            headers={"X-Onboarding-Secret": onboarding_secret},
            timeout=10,
        )
        return {
            "success": response.status_code == 201,
            "status_code": response.status_code,
            "response": response.text,
        }

    except (OSError, subprocess.SubprocessError):
        return {
            "success": False,
            "status_code": None,
            "response": "Could not read the Federation onboarding secret.",
        }
    except requests.RequestException:
        return {
            "success": False,
            "status_code": None,
            "response": "Could not connect to GNU Health Federation.",
        }


def get_gnuhealth_wellness(person_id, username, password):
    """
    Retrieve Pages of Life from Thalamus for a Federation ID.

    Note: Thalamus must enforce that the authenticated account is authorized
    to read the requested person_id. This function alone cannot enforce that.
    """

    if not person_id or not username or not password:
        return {
            "success": False,
            "records": [],
            "status_code": None,
            "error": "Federation credentials are required.",
        }

    try:
        response = requests.get(
            f"{THALAMUS_URL}/pols/{person_id}",
            auth=(username, password),
            timeout=15,
        )

        if response.status_code == 401:
            return {
                "success": False,
                "records": [],
                "status_code": 401,
                "error": "Federation authentication failed.",
            }

        response.raise_for_status()
        records = response.json()

        return {
            "success": True,
            "records": records,
            "status_code": response.status_code,
            "error": None,
        }

    except requests.RequestException:
        return {
            "success": False,
            "records": [],
            "status_code": None,
            "error": "Could not retrieve Federation wellness records.",
        }
    except ValueError:
        return {
            "success": False,
            "records": [],
            "status_code": None,
            "error": "Federation returned invalid JSON.",
        }



def send_wellness_to_gnuhealth(
    person_id, username=None, password=None,
    sleep_hours=None, mood=None, energy=None
):
    """Save wellness through the trusted Empath AI Thalamus endpoint."""
    import uuid
    import subprocess
    from datetime import datetime
    import requests

    if not person_id:
        return {
            "success": False,
            "status_code": None,
            "response": "Federation ID is required."
        }

    measurements = []

    if mood is not None or energy is not None:
        measurements.append({
            "mood_energy": {"mood": mood, "energy": energy}
        })

    if sleep_hours is not None:
        measurements.append({
            "sleep": {
                "sleeptime": sleep_hours,
                "sleepquality": "good" if sleep_hours >= 6 else "poor"
            }
        })

    if not measurements:
        return {
            "success": False,
            "status_code": None,
            "response": "No wellness measurements to save."
        }

    try:
        result = subprocess.run(
            [
                "wsl.exe", "-d", "Ubuntu-24.04", "--",
                "bash", "-lc",
                "cat ~/.config/empath-ai/onboarding_secret"
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True
        )
        secret = result.stdout.strip()

        if not secret:
            return {
                "success": False,
                "status_code": None,
                "response": "Thalamus backend secret is empty."
            }

        page_id = f"wellness_{uuid.uuid4().hex[:12]}"
        page = {
            "id": page_id,
            "page": page_id,
            "page_date": datetime.now().isoformat(),
            "book": person_id,
            "domain": "medical",
            "context": "self_monitoring",
            "measurements": measurements,
            "summary": "",
            "info": "Empath AI wellness"
        }

        response = requests.post(
            f"{THALAMUS_URL}/empath/wellness",
            json={"person_id": person_id, "page": page},
            headers={"X-Onboarding-Secret": secret},
            timeout=15
        )

        return {
            "success": response.status_code == 201,
            "status_code": response.status_code,
            "response": (
                response.text if not response.ok
                else "Federation wellness record saved."
            )
        }

    except (OSError, subprocess.SubprocessError):
        return {
            "success": False,
            "status_code": None,
            "response": "Could not read the Thalamus backend secret."
        }
    except requests.RequestException:
        return {
            "success": False,
            "status_code": None,
            "response": "Could not connect to GNU Health Federation."
        }


def create_mygnuhealth_profile(federation_id):
    """Initialize a separate local MyGNUHealth profile for a Federation ID."""
    import re
    import subprocess

    if not federation_id:
        return {"success": False, "error": "Federation ID is required."}

    profile_id = str(federation_id).strip().upper()

    # Restrict directory names to safe ID characters.
    if not re.fullmatch(r"[A-Z0-9_-]{1,64}", profile_id):
        return {"success": False, "error": "Invalid Federation ID."}

    profile_path = f"/home/usman/mygnuhealth_profiles/{profile_id}"

    script = (
        "from mygnuhealth.core import maindb, boldb\n"
        "print('MyGNUHealth profile initialized successfully.')\n"
    )

    try:
        result = subprocess.run(
            [
                "wsl.exe", "-d", "Ubuntu-24.04", "--",
                "env", f"XDG_CONFIG_HOME={profile_path}",
                "/home/usman/.local/share/pipx/venvs/pip/bin/python",
                "-c", script,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            return {
                "success": False,
                "error": (result.stderr or "Profile initialization failed.")[-1500:],
            }

        return {
            "success": True,
            "profile_id": profile_id,
            "profile_path": profile_path,
        }

    except (OSError, subprocess.SubprocessError) as exc:
        return {"success": False, "error": str(exc)}


def update_mygnuhealth_tracker(
    profile,
    sleep_hours=None,
    mood=None,
    energy=None,
):
    """
    Update an existing local MyGNUHealth profile's heart trackers and Book
    of Life. This function currently supports only the two profile paths
    listed below; it is not yet dynamic for newly registered users.
    """

    script = r"""
from mygnuhealth.core import maindb, boldb
from datetime import datetime
import uuid
import sys

sleep_hours = None if sys.argv[1] == "None" else float(sys.argv[1])
mood = None if sys.argv[2] == "None" else sys.argv[2]
energy = None if sys.argv[3] == "None" else sys.argv[3]
federation_id = sys.argv[4]

measurements = []

if mood is not None or energy is not None:
    maindb.table("mood").insert({
        "timestamp": datetime.now().isoformat(),
        "event_id": str(uuid.uuid4()),
        "synced": False,
        "mood": mood,
        "energy": energy,
    })
    measurements.append({
        "mood_energy": {"mood": mood, "energy": energy}
    })

if sleep_hours is not None:
    sleep_quality = "good" if sleep_hours >= 6 else "poor"
    maindb.table("sleep").insert({
        "timestamp": datetime.now().isoformat(),
        "event_id": str(uuid.uuid4()),
        "synced": False,
        "sleeptime": sleep_hours,
        "sleepquality": sleep_quality,
    })
    measurements.append({
        "sleep": {
            "sleeptime": sleep_hours,
            "sleepquality": sleep_quality,
        }
    })

if measurements:
    page_id = "wellness_" + uuid.uuid4().hex[:12]
    boldb.table("pol").insert({
        "book": federation_id,
        "page_date": datetime.now().isoformat(),
        "age": None,
        "domain": "medical",
        "relevance": None,
        "privacy": None,
        "context": "self_monitoring",
        "measurements": measurements,
        "genetic_info": "",
        "summary": "",
        "info": "Empath AI wellness",
        "node": "mygnuhealth",
        "author": federation_id,
        "author_acct": federation_id,
        "fsynced": True,
        "page": page_id,
    })
    print("Book of Life record created:", page_id)

print("MyGNUHealth tracker and Book of Life update completed.")
"""

    # These are the existing local profiles. New users need a provisioning
    # step before this function can safely target their own local database.
   
    if not profile:
        print("Missing MyGNUHealth profile ID.")
        return False

    federation_id = str(profile).strip().upper()

    # Accept only safe Federation IDs such as EMPATH_11.
    if not re.fullmatch(r"[A-Z0-9_-]{1,64}", federation_id):
        print("Invalid MyGNUHealth profile ID:", federation_id)
        return False

    # Standard profile path for automatically created Empath AI users.
    xdg_config = (
        f"/home/usman/mygnuhealth_profiles/{federation_id}"
    )


    try:
        result = subprocess.run(
            [
                "wsl.exe",
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
                federation_id,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.stdout:
            print(result.stdout)

        if result.returncode != 0:
            print("MyGNUHealth update failed:", result.stderr)
            return False

        return True

    except (OSError, subprocess.SubprocessError) as exc:
        print("MyGNUHealth update error:", exc)
        return False


import json
import re
import subprocess


WSL_DISTRO = "Ubuntu-24.04"

WSL_PYTHON = (
    "/home/usman/.local/share/pipx/venvs/pip/bin/python"
)

PROFILE_ROOT = "/home/usman/mygnuhealth_profiles"


def _run_wsl(profile_id, script, payload=None, timeout=60):
    """Run a Python script against one isolated MyGNUHealth profile."""

    profile_id = str(profile_id or "").strip().upper()

    if not re.fullmatch(r"[A-Z0-9_-]{1,64}", profile_id):
        return None

    profile_path = f"{PROFILE_ROOT}/{profile_id}"

    try:
        result = subprocess.run(
            [
                "wsl.exe",
                "-d", WSL_DISTRO,
                "--",
                "env",
                f"XDG_CONFIG_HOME={profile_path}",
                WSL_PYTHON,
                "-c",
                script,
            ],
            input=json.dumps(payload or {}),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0:
            print("MyGNUHealth error:", result.stderr[-1500:])
            return None

        return json.loads(result.stdout.strip().splitlines()[-1])

    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print("MyGNUHealth operation failed:", str(exc))
        return None


def create_mygnuhealth_profile(profile_id, name, dob, password):
    """
    Initialize a separate local MyGNUHealth profile.

    The Empath AI password is hashed separately using bcrypt.
    Plaintext password is passed through subprocess stdin, not arguments.
    """

    profile_id = str(profile_id or "").strip().upper()
    name = str(name or "").strip()
    dob = str(dob or "").strip()

    if not re.fullmatch(r"[A-Z0-9_-]{1,64}", profile_id):
        return {"success": False, "error": "Invalid profile ID."}

    if not name or not dob or not password:
        return {"success": False, "error": "Missing profile information."}

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dob):
        return {"success": False, "error": "Invalid date format."}

    script = r"""
import sys
import json
import bcrypt
from datetime import date
from mygnuhealth.core import maindb, boldb
from mygnuhealth.myghconf import dbfile, bolfile

data = json.load(sys.stdin)

name = data["name"]
dob = data["dob"]
password = data["password"]

# Validate the date.
date.fromisoformat(dob)

profile_table = maindb.table("profile")
credentials_table = maindb.table("credentials")

profiles = profile_table.all()
credentials = credentials_table.all()

# Support safe retries without overwriting an existing account.
if profiles or credentials:

    if len(profiles) != 1 or len(credentials) != 1:
        raise RuntimeError(
            "Profile is partially initialized. Manual recovery required."
        )

    stored_hash = credentials[0].get("personal_key", "").encode("utf-8")

    if not stored_hash or not bcrypt.checkpw(
        password.encode("utf-8"), stored_hash
    ):
        raise RuntimeError(
            "Existing profile credentials do not match."
        )

    existing_profile = profiles[0]

    if (
        existing_profile.get("username") != name
        or existing_profile.get("birthdate") != dob
    ):
        raise RuntimeError(
            "Existing profile information does not match."
        )

    print(json.dumps({
        "success": True,
        "already_initialized": True,
        "dbfile": dbfile,
        "bolfile": bolfile
    }))
    sys.exit(0)

# Create separate bcrypt hash for MyGNUHealth.
personal_key_hash = bcrypt.hashpw(
    password.encode("utf-8"),
    bcrypt.gensalt()
).decode("utf-8")

credentials_table.insert({
    "personal_key": personal_key_hash
})

profile_table.insert({
    "username": name,
    "birthdate": dob
})

print(json.dumps({
    "success": True,
    "already_initialized": False,
    "dbfile": dbfile,
    "bolfile": bolfile
}))
"""

    try:
        result = _run_wsl(
            profile_id,
            script,
            {
                "name": name,
                "dob": dob,
                "password": password,
            },
        )

        if not result or not result.get("success"):
            return {
                "success": False,
                "error": "Could not initialize local MyGNUHealth profile."
            }

        return {
            "success": True,
            "profile_id": profile_id,
            "already_initialized": result.get("already_initialized", False),
        }

    except Exception as exc:
        print("Profile creation failed:", str(exc))
        return {"success": False, "error": str(exc)}


def update_mygnuhealth_tracker(
    profile,
    sleep_hours=None,
    mood=None,
    energy=None,
):
    """Save wellness data to the correct local MyGNUHealth profile."""

    profile_id = str(profile or "").strip().upper()

    if not re.fullmatch(r"[A-Z0-9_-]{1,64}", profile_id):
        print("Invalid MyGNUHealth profile ID.")
        return False

    if sleep_hours is None and mood is None and energy is None:
        return True

    script = r"""
import sys
import json
import uuid
from datetime import datetime
from mygnuhealth.core import maindb, boldb

data = json.load(sys.stdin)

sleep_hours = data.get("sleep_hours")
mood = data.get("mood")
energy = data.get("energy")
profile_id = data["profile_id"]

now = datetime.now().isoformat()
measurements = []

# Save mood and energy in the native mood tracker.
if mood is not None or energy is not None:

    maindb.table("mood").insert({
        "timestamp": now,
        "event_id": str(uuid.uuid4()),
        "synced": False,
        "mood": mood,
        "energy": energy,
    })

    measurements.append({
        "mood_energy": {
            "mood": mood,
            "energy": energy
        }
    })

# Save sleep in the native sleep tracker.
if sleep_hours is not None:

    sleep_hours = float(sleep_hours)

    sleep_quality = "good" if sleep_hours >= 6 else "poor"

    maindb.table("sleep").insert({
        "timestamp": now,
        "event_id": str(uuid.uuid4()),
        "synced": False,
        "sleeptime": sleep_hours,
        "sleepquality": sleep_quality,
    })

    measurements.append({
        "sleep": {
            "sleeptime": sleep_hours,
            "sleepquality": sleep_quality
        }
    })

# Save a private, local Book of Life page.
if measurements:

    page_id = "wellness_" + uuid.uuid4().hex[:12]

    boldb.table("pol").insert({
        "book": profile_id,
        "page_date": now,
        "age": None,
        "domain": "medical",
        "relevance": None,
        "privacy": True,
        "context": "self_monitoring",
        "measurements": measurements,
        "genetic_info": "",
        "summary": "Empath AI wellness",
        "info": "Empath AI wellness",
        "node": "mygnuhealth",
        "author": profile_id,
        "author_acct": profile_id,
        "fsynced": False,
        "page": page_id,
    })

print(json.dumps({
    "success": True,
    "page_id": page_id if measurements else None
}))
"""

    result = _run_wsl(
        profile_id,
        script,
        {
            "profile_id": profile_id,
            "sleep_hours": sleep_hours,
            "mood": mood,
            "energy": energy,
        },
        timeout=30,
    )

    if not result or not result.get("success"):
        return False

    print("Local MyGNUHealth wellness saved successfully.")
    return True

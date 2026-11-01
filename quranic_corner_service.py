
import random
import requests
import re
import html
import json

BASE_URL = "https://api.quranpedia.net/v1"
TIMEOUT = 15

# Verified Quranpedia translation book IDs
ENGLISH_TRANSLATION_BOOK_ID = 1947  # Saheeh International
URDU_TRANSLATION_BOOK_ID = 13625    # Muhammad Junagarhi

CATEGORY_NAMES = {
    "anxiety",
    "sadness",
    "hopelessness",
    "fear",
    "loneliness",
    "gratitude"
}


def api_get(endpoint):
    """Fetch Quranic content from Quranpedia."""
    url = f"{BASE_URL}/{endpoint.lstrip('/')}"

    response = requests.get(url, timeout=TIMEOUT)
    response.raise_for_status()

    return response.json()


def clean_html(text):
    """Clean translation HTML and remove separate footnotes."""

    if not text:
        return ""

    # Remove footnote sections before cleaning HTML
    text = re.sub(
        r"<div[^>]*class=['\"]foot-notes['\"][^>]*>.*?</div>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL
    )

    text = re.sub(
        r"<br\s*/?>",
        " ",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(r"<[^>]+>", "", text)

    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()

def get_arabic_ayah(surah_number, ayah_number):
    """Retrieve original Arabic text from Hafs Mushaf."""
    data = api_get(
        f"mushafs/1/{surah_number}/{ayah_number}"
    )

    arabic = data.get("text", "")

    # Remove an invisible BOM character if present.
    return arabic.lstrip("\ufeff").strip()


def get_translation(surah_number, ayah_number, language):
    """Retrieve a verified Quran translation."""

    if language == "en":
        book_id = ENGLISH_TRANSLATION_BOOK_ID
    elif language == "ur":
        book_id = URDU_TRANSLATION_BOOK_ID
    else:
        raise ValueError("Language must be 'en' or 'ur'.")

    data = api_get(
        f"translation/{book_id}/{surah_number}/{ayah_number}"
    )

    if not isinstance(data, dict):
        return None

    translation = data.get("translation_text", "")

    return clean_html(translation) or None


def get_maududi_tafseer(surah_number, ayah_number):
    """
    Maududi Tafseer is temporarily disabled.

    Quranpedia search has not yet confirmed the exact
    Tafheem-ul-Quran book ID and author metadata.

    Never substitute another Tafseer as Maududi Tafseer.
    """

    return None


def load_category_references(json_path):
    """Read category mappings from the JSON file."""

    with open(json_path, "r", encoding="utf-8") as file:
        return json.load(file)



def get_category_verses(
    json_path,
    category,
    count=3,
    used_ids=None
):
    """
    Select random Ayat from any Quranic Comfort Corner
    category and retrieve Arabic, English and Urdu content.
    """

    category = str(category).strip().lower()

    allowed_categories = {
        "anxiety",
        "sadness",
        "hopelessness",
        "fear",
        "loneliness",
        "gratitude"
    }

    if category not in allowed_categories:
        raise ValueError("Invalid Quranic Corner category.")

    if count not in (1, 2, 3):
        raise ValueError("Count must be 1, 2, or 3.")

    # Load JSON references
    content = load_category_references(json_path)

    categories = content.get("categories", {})
    all_verses = content.get("verses", [])

    # Get IDs for selected category
    verse_ids = categories.get(category, [])

    print("\n========== QURANIC VERSE DEBUG ==========")
    print("SELECTED CATEGORY:", category)
    print("CATEGORY IDS FOUND:", len(verse_ids))
    print("TOTAL VERSE RECORDS:", len(all_verses))

    # Create verse lookup dictionary
    verse_records = {
        verse["id"]: verse
        for verse in all_verses
        if isinstance(verse, dict) and verse.get("id")
    }

    # Match category IDs with available verse records
    available = [
        verse_records[verse_id]
        for verse_id in verse_ids
        if verse_id in verse_records
    ]

    print("MATCHED VERSES:", len(available))

    if not available:
        print("WARNING: No matching verses found.")
        print("========================================\n")
        return []

    # Exclude previously used Ayat when possible
    used_ids = set(used_ids or [])

    fresh = [
        verse for verse in available
        if verse.get("id") not in used_ids
    ]

    if fresh:
        available = fresh

    # Randomly select Ayat
    selected = random.sample(
        available,
        min(count, len(available))
    )

    results = []

    for verse in selected:
        try:
            surah = verse["surah_number"]
            ayah = verse["ayah_number"]

            arabic = get_arabic_ayah(surah, ayah)
            translation_en = get_translation(
                surah, ayah, "en"
            )
            translation_ur = get_translation(
                surah, ayah, "ur"
            )

            tafseer_ur = get_maududi_tafseer(
                surah, ayah
            )

            results.append({
                "id": verse["id"],
                "surah_number": surah,
                "surah_name": verse["surah_name"],
                "ayah_number": ayah,
                "arabic": arabic,
                "translation_en": translation_en,
                "translation_ur": translation_ur,
                "tafseer_ur": tafseer_ur,
                "source": "Quranpedia API"
            })

            print("SUCCESSFULLY LOADED:", verse["id"])

        except Exception as verse_error:
            print(
                "ERROR LOADING VERSE:",
                verse.get("id"),
                str(verse_error)
            )
            continue

    print("FINAL RETURNED VERSES:", len(results))
    print("========================================\n")

    return results

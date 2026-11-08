"""Empath AI: start the server with `python app.py` (same as before).

All the code now lives in the empath/ folder, one file per topic."""

from empath import app  # noqa: F401  (also registers every route)
from empath.context import extract_energy, extract_wellbeing_info
from empath.database import init_db

    
# ==============================
# RUN APP
# ==============================
if __name__ == "__main__":
    with app.app_context():
        init_db()
    app.run(
    debug=True,
    port=5000,
    use_reloader=False
)
    
    test_message = "کبھی کبھی اتنی تھکن ہوتی ہے کہ کچھ کرنے کا دل نہیں کرتا۔"

    print("ENERGY:", extract_energy(test_message))
    print("WELLBEING:", extract_wellbeing_info(test_message))

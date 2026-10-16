/*
 * ============================================================
 * EMPATH AI — SHARED LANGUAGE STATE
 * ============================================================
 *
 * IMPORTANT:
 * The language is selected ONLY on index.html.
 *
 * The user does NOT select the language again on:
 * Login
 * Registration
 * Dashboard
 * Assessment
 * Wellness
 * Chatbot
 * Quotes
 * etc.
 *
 * The selected language is saved in:
 *
 *     localStorage["empathLanguage"]
 *
 * English = "en"
 * Urdu    = "ur"
 *
 * ============================================================
 */

const EMPATH_LANGUAGE_KEY = "empathLanguage";


/*
 * Get the language selected by the user
 *
 * If no language has been selected yet,
 * English will be used as default.
 */
function getEmpathLanguage() {

    return localStorage.getItem(EMPATH_LANGUAGE_KEY) || "en";

}


/*
 * Check whether the selected language is Urdu
 */
function isEmpathUrdu() {

    return getEmpathLanguage() === "ur";

}


/*
 * Apply the selected language direction
 *
 * English → Left to Right
 * Urdu    → Right to Left
 */
function setEmpathPageLanguage() {

    const language = getEmpathLanguage();

    document.documentElement.lang = language;

    if (language === "ur") {

        document.documentElement.dir = "rtl";

    } else {

        document.documentElement.dir = "ltr";

    }

    return language;

}


/*
 * Automatically apply the saved language
 * whenever a page loads.
 */
document.addEventListener("DOMContentLoaded", function () {

    setEmpathPageLanguage();

});
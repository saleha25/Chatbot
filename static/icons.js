/* =========================================================
   EMPATH AI — ICON LIBRARY
   ---------------------------------------------------------
   All icons used on the pages live in this one file.

   Usage in HTML:
     <span data-icon="chat"></span>

   Usage in JS:
     empathIcon("bell")   -> returns the <svg> as a string

   Icons inherit the text colour (currentColor) and the size
   of their container, so style the <span> with CSS.
   ========================================================= */
(function () {
  "use strict";

  var STROKE =
    'fill="none" stroke="currentColor" stroke-width="1.8" ' +
    'stroke-linecap="round" stroke-linejoin="round"';

  var ICONS = {
    /* Brand mark (filled) */
    logo:
      '<svg viewBox="0 0 18 18" fill="currentColor">' +
      '<circle cx="9" cy="7" r="4"/><rect x="7" y="11" width="4" height="5" rx="1"/></svg>',

    /* Dashboard cards */
    chat:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>' +
      '<path d="M8.5 12h.01M12 12h.01M15.5 12h.01"/></svg>',

    assessment:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<rect x="5" y="4" width="14" height="18" rx="2"/>' +
      '<path d="M9 2h6v4H9z"/><path d="M9 13l2 2 4-4"/></svg>',

    wellness:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M3 17l6-6 4 4 8-8"/><path d="M14 7h7v7"/></svg>',

    hope:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M12 3v3M4.9 7.9l1.4 1.4M19.1 7.9l-1.4 1.4M2 16h2M20 16h2"/>' +
      '<path d="M16 16a4 4 0 0 0-8 0"/><path d="M2 20h20"/></svg>',

    quran:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M20.5 13.5A8.5 8.5 0 1 1 10.5 3.5a6.5 6.5 0 0 0 10 10z"/></svg>',

    /* Wellness page */
    sleep:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M2 18V8M2 13h20v5M22 18v-3"/><path d="M6 13V11a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v2"/></svg>',

    mood:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0"/><path d="M9 9.5h.01M15 9.5h.01"/></svg>',

    energy:
      '<svg viewBox="0 0 24 24" ' + STROKE + '><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/></svg>',

    phone:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.4 1.8.7 2.7a2 2 0 0 1-.5 2.1L8 9.8a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.7.7a2 2 0 0 1 1.7 2z"/></svg>',

    /* Chat page */
    mic:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v4M8 22h8"/></svg>',

    stop:
      '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2.5"/></svg>',

    send:
      '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M3.4 20.4l17.4-7.5a1 1 0 0 0 0-1.8L3.4 3.6a1 1 0 0 0-1.4 1.2L4.5 12 2 19.2a1 1 0 0 0 1.4 1.2z"/></svg>',

    /* Words of Hope page */
    refresh:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M20 12a8 8 0 1 1-2.3-5.7"/><path d="M20 4v5h-5"/></svg>',

    /* Quranic Comfort Corner page */
    book:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M3 5c4-2 7-1 9 1v15c-2-2-5-3-9-1z"/><path d="M21 5c-4-2-7-1-9 1v15c2-2 5-3 9-1z"/></svg>',

    pen:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L9 17l-4 1 1-4z"/><path d="M14 5l5 5"/></svg>',

    /* Calm Corner page */
    calm:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M3 8h11a3 3 0 1 0-3-3"/><path d="M3 12h15a3 3 0 1 1-3 3"/><path d="M3 16h7"/></svg>',

    /* Report page */
    report:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>' +
      '<path d="M9 17v-3M12 17v-6M15 17v-4"/></svg>',

    download:
      '<svg viewBox="0 0 24 24" ' + STROKE + '><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/></svg>',

    printer:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M6 9V3h12v6"/><rect x="3" y="9" width="18" height="8" rx="2"/><path d="M6 14h12v7H6z"/></svg>',

    feelAnxiety:
      '<svg viewBox="0 0 48 48" ' + STROKE + '>' +
      '<path d="M24 7C17 7 12 12 12 19C8 20 7 25 10 29C9 34 13 37 18 36C20 40 25 40 27 36C33 38 38 34 37 29C41 25 39 20 35 19C35 12 30 7 24 7Z"/>' +
      '<path d="M24 8V37"/><path d="M17 17C13 20 15 24 19 25"/><path d="M30 16C34 19 32 23 29 25"/>' +
      '<path d="M17 30C19 27 22 29 22 32"/><path d="M31 30C29 27 26 29 26 32"/></svg>',

    feelSadness:
      '<svg viewBox="0 0 48 48" ' + STROKE + '>' +
      '<path d="M24 6C18 15 10 23 10 31a14 14 0 0 0 28 0C38 23 30 15 24 6Z"/><path d="M18 32c1 4 4 6 8 6"/></svg>',

    feelHopeless:
      '<svg viewBox="0 0 48 48" ' + STROKE + '>' +
      '<path d="M12 29a10 10 0 0 1 1-19 12 12 0 0 1 22 4 9 9 0 0 1 1 18H13"/>' +
      '<path d="M17 36l-3 6"/><path d="M25 36l-3 6"/><path d="M33 36l-3 6"/></svg>',

    feelFear:
      '<svg viewBox="0 0 48 48" ' + STROKE + '>' +
      '<path d="M24 5 39 11v11c0 10-6 16-15 21C15 38 9 32 9 22V11L24 5Z"/><path d="M24 15v11"/><circle cx="24" cy="32" r="1"/></svg>',

    feelLonely:
      '<svg viewBox="0 0 48 48" ' + STROKE + '>' +
      '<circle cx="24" cy="13" r="7"/><path d="M10 40v-4a14 14 0 0 1 28 0v4"/><path d="M17 39v-4"/><path d="M31 39v-4"/></svg>',

    feelGratitude:
      '<svg viewBox="0 0 48 48" ' + STROKE + '>' +
      '<path d="M24 39S7 28 7 17a9 9 0 0 1 17-4 9 9 0 0 1 17 4c0 11-17 22-17 22Z"/>' +
      '<path d="M5 37c3-2 5-5 8-5"/><path d="M43 37c-3-2-5-5-8-5"/><path d="M17 28l7 7 7-7"/></svg>',

    /* Navigation */
    home:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/>' +
      '<rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',

    menu:
      '<svg viewBox="0 0 24 24" ' + STROKE + '><path d="M4 7h16M4 12h16M4 17h10"/></svg>',

    close:
      '<svg viewBox="0 0 24 24" ' + STROKE + '><path d="M6 6l12 12M18 6L6 18"/></svg>',

    panel:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<rect x="3" y="4" width="18" height="16" rx="2.5"/><path d="M9 4v16"/><path d="M15 10l-2 2 2 2"/></svg>',

    /* UI */
    arrow:
      '<svg viewBox="0 0 24 24" ' + STROKE + '><path d="M5 12h14M13 6l6 6-6 6"/></svg>',

    chevron:
      '<svg viewBox="0 0 24 24" ' + STROKE + '><path d="M6 9l6 6 6-6"/></svg>',

    bell:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/>' +
      '<path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>',

    check:
      '<svg viewBox="0 0 24 24" ' + STROKE + '><path d="M20 6L9 17l-5-5"/></svg>',

    logout:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>' +
      '<path d="M16 17l5-5-5-5M21 12H9"/></svg>',

    trash:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/></svg>',

    lock:
      '<svg viewBox="0 0 24 24" ' + STROKE + '>' +
      '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>'
  };

  function sized(svg) {
    // make every icon fill its container
    return svg.replace("<svg ", '<svg width="100%" height="100%" aria-hidden="true" focusable="false" ');
  }

  window.EMPATH_ICONS = ICONS;

  window.empathIcon = function (name) {
    return ICONS[name] ? sized(ICONS[name]) : "";
  };

  window.renderEmpathIcons = function (root) {
    (root || document).querySelectorAll("[data-icon]").forEach(function (el) {
      var name = el.getAttribute("data-icon");
      if (ICONS[name] && el.getAttribute("data-icon-done") !== name) {
        el.innerHTML = sized(ICONS[name]);
        el.setAttribute("data-icon-done", name);
      }
    });
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { window.renderEmpathIcons(); });
  } else {
    window.renderEmpathIcons();
  }
})();
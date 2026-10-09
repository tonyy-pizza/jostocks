r"""jos/theme.py - the palette, the fonts and the stylesheets.

The colours are Dionysus's, verified against the "3b" artboard rather than
transcribed from the brief: every value below appears in that file. Two shades
the brief does not name are in it and needed - ROW_LINE for the divider
between table rows, and FAINT for text that has to recede further than MUTED.

Rules this encodes, from section 5:

  - Hard corners. No border-radius anywhere, which is why there is no radius
    constant to get wrong.
  - Lowercase panel titles, set in Fraunces. Body in Inter. Numbers in
    JetBrains Mono, which is also the only font the tables use, so columns of
    figures line up.
  - Colour never carries meaning alone. SCORE_* are paired with the glyphs and
    words the pipeline's own report uses, and score_colour() has a matching
    score_band() that returns a word.
  - A web-view panel gets a dashed amber border and a "web" tag; a native one
    gets a solid border and a "native" tag.

Fonts are loaded from fonts/ with QFontDatabase.addApplicationFont so the
window looks the same on a machine that has none of them installed, and
font_families() reports what actually loaded rather than assuming.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QColor, QFont, QFontDatabase

from .paths import FONT_DIR

# ─── PALETTE ─────────────────────────────────────────────────────────────
BG = "#0B1120"            # window ground
PANEL_BG = "#141B2E"      # panel fill
PANEL_BORDER = "#1F2942"  # panel edge, and table header rules
CONTROL_BORDER = "#3A4566"  # sliders, inputs, buttons
ROW_LINE = "#1A2238"      # between table rows: softer than PANEL_BORDER
CREAM = "#F0E6D2"         # primary text
MUTED = "#8A93A8"         # labels, column headers, secondary text
FAINT = "#5B6580"         # text that must recede further still
SELECTED = "#1B2540"      # selected-row tint

AMBER = "#E3B341"         # yellow score, reductions, threshold line, web tag
GREEN = "#7FB77E"         # good score, pass
RED = "#E06C5B"           # bad score, fail
CYAN = "#6CC4D8"          # held, industry_wide

#: Single hue for the heatmap. Alpha carries the value, as section 5 requires.
HEATMAP_RGB = (111, 168, 220)

# ─── TYPE ────────────────────────────────────────────────────────────────
TITLE_FAMILY = "Fraunces"
BODY_FAMILY = "Inter"
MONO_FAMILY = "JetBrains Mono"

# Fallbacks in the order a machine is likely to have them. Fraunces is a
# serif, so it falls back to serifs; the mono list ends at the two Windows
# ships with and the one macOS does.
_FALLBACKS = {
    TITLE_FAMILY: ("Fraunces", "Georgia", "Times New Roman", "serif"),
    BODY_FAMILY: ("Inter", "Segoe UI", "Helvetica Neue", "sans-serif"),
    MONO_FAMILY: ("JetBrains Mono", "Cascadia Mono", "Consolas",
                  "SF Mono", "Menlo", "monospace"),
}

TITLE_PX = 15
BODY_PX = 12
CAPTION_PX = 11
MONO_PX = 12

_loaded: dict = {}


def load_fonts(font_dir=None) -> dict:
    """Load every font file in fonts/ and report which families arrived.

    Bundling them is section 9b's requirement: the look must not depend on
    what the machine happens to have installed. A missing folder is not an
    error - the fallbacks below cover it and the app says so in the README
    rather than refusing to start.
    """
    global _loaded
    if _loaded:
        return _loaded

    directory = Path(font_dir) if font_dir else FONT_DIR
    families: dict = {}
    if directory.is_dir():
        for path in sorted(directory.rglob("*")):
            if path.suffix.lower() not in (".ttf", ".otf", ".woff", ".woff2"):
                continue
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id == -1:
                continue
            for family in QFontDatabase.applicationFontFamilies(font_id):
                families[family] = path.name

    _loaded = families
    return families


def _resolve(wanted: str) -> str:
    """A CSS-style family list, best available first.

    Qt takes a comma-separated list and walks it, so naming the bundled family
    first and the system ones after means one string works whether the .ttf
    files are present or not.
    """
    available = set(QFontDatabase.families())
    chain = [f for f in _FALLBACKS.get(wanted, (wanted,))
             if f in available or f in ("serif", "sans-serif", "monospace")]
    if wanted in available and (not chain or chain[0] != wanted):
        chain.insert(0, wanted)
    return ", ".join(chain) if chain else wanted


def font_families() -> dict:
    """{"title": ..., "body": ..., "mono": ...} as CSS family lists."""
    return {"title": _resolve(TITLE_FAMILY),
            "body": _resolve(BODY_FAMILY),
            "mono": _resolve(MONO_FAMILY)}


def font(kind="body", px=None, bold=False) -> QFont:
    """A QFont for painted text, where a stylesheet cannot reach."""
    family = {"title": TITLE_FAMILY, "body": BODY_FAMILY,
              "mono": MONO_FAMILY}.get(kind, BODY_FAMILY)
    available = set(QFontDatabase.families())
    for option in _FALLBACKS.get(family, (family,)):
        if option in available:
            family = option
            break
    out = QFont(family)
    out.setPixelSize(px or {"title": TITLE_PX, "body": BODY_PX,
                            "mono": MONO_PX}.get(kind, BODY_PX))
    out.setBold(bold)
    if kind == "mono":
        # Figures must line up column to column, which proportional digits
        # defeat even in a monospaced face when the face has them.
        out.setStyleHint(QFont.StyleHint.Monospace)
    return out


# ─── SCORE COLOUR ────────────────────────────────────────────────────────
# Thresholds come from stock_evaluator through jos.pipeline, never from a
# literal here: the brief's "green >= 7.5" is stale by one recalibration and
# the point of this function is that the app and the terminal report agree.

def score_colour(value, thresholds=None) -> str:
    """The hex colour scan_report.colour() would have used."""
    from . import pipeline as pipe
    if value is None:
        return FAINT
    good, fair = thresholds or pipe.colour_thresholds()
    if value >= good:
        return GREEN
    return AMBER if value >= fair else RED


def score_band(value, thresholds=None) -> str:
    """The same judgement as a word, so colour is never the only carrier."""
    from . import pipeline as pipe
    if value is None:
        return "n/a"
    good, fair = thresholds or pipe.colour_thresholds()
    if value >= good:
        return "good"
    return "fair" if value >= fair else "weak"


def heatmap_colour(value) -> str:
    """rgba() for a correlation, on the single hue section 5 specifies.

    Negative correlations clamp to the low end of the ramp - they are not
    duplicate positions - but the caller keeps the true number for the
    tooltip, which is where the sign still matters.
    """
    from . import pipeline as pipe
    number = pipe.num(value)
    alpha = 0.0 if number is None else max(0.0, min(1.0, number))
    r, g, b = HEATMAP_RGB
    return f"rgba({r},{g},{b},{alpha:.3f})"


def mix(first, second, amount) -> str:
    """Blend two hex colours. For bars and hover states."""
    a, b = QColor(first), QColor(second)
    ratio = max(0.0, min(1.0, amount))
    return QColor(
        round(a.red() + (b.red() - a.red()) * ratio),
        round(a.green() + (b.green() - a.green()) * ratio),
        round(a.blue() + (b.blue() - a.blue()) * ratio),
    ).name()


# ─── STYLESHEET ──────────────────────────────────────────────────────────

def app_stylesheet() -> str:
    """One stylesheet for the whole app.

    Hard corners everywhere. The scrollbars are styled because the default
    ones are the single most obviously un-themed thing in a dark Qt window.
    """
    f = font_families()
    return f"""
QWidget {{
    background: {BG};
    color: {CREAM};
    font-family: {f['body']};
    font-size: {BODY_PX}px;
}}

QToolTip {{
    background: {PANEL_BG};
    color: {CREAM};
    border: 1px solid {CONTROL_BORDER};
    padding: 4px 6px;
}}

/* ── panels ───────────────────────────────────────────────────────── */
QFrame#panel {{
    background: {PANEL_BG};
    border: 1px solid {PANEL_BORDER};
}}
QFrame#panel[webview="true"] {{
    border: 1px dashed {AMBER};
}}

QLabel#panelTitle {{
    font-family: {f['title']};
    font-size: {TITLE_PX}px;
    color: {CREAM};
    background: transparent;
    padding: 0;
}}
QLabel#panelTag {{
    font-family: {f['mono']};
    font-size: 9px;
    color: {MUTED};
    background: transparent;
    border: 1px solid {PANEL_BORDER};
    padding: 1px 4px;
}}
QLabel#panelTag[webview="true"] {{
    color: {AMBER};
    border: 1px solid {AMBER};
}}
QLabel#caption {{ color: {MUTED}; font-size: {CAPTION_PX}px; background: transparent; }}
QLabel#faint   {{ color: {FAINT};  font-size: {CAPTION_PX}px; background: transparent; }}
QLabel#mono    {{ font-family: {f['mono']}; background: transparent; }}

/* The dropdown that *is* the panel title. Styled to read as the heading it
   replaces: no frame, no field, just the word and a caret. */
QComboBox#titleCombo {{
    font-family: {f['title']};
    font-size: {TITLE_PX}px;
    color: {CREAM};
    background: transparent;
    border: none;
    padding: 0 18px 0 0;
}}
QComboBox#titleCombo::drop-down {{
    border: none;
    width: 14px;
    subcontrol-position: center right;
}}
QComboBox#titleCombo::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {AMBER};
    width: 0; height: 0;
    margin-right: 2px;
}}
QComboBox#titleCombo:hover {{ color: {AMBER}; }}
QComboBox QAbstractItemView {{
    background: {PANEL_BG};
    color: {CREAM};
    border: 1px solid {CONTROL_BORDER};
    selection-background-color: {SELECTED};
    outline: none;
    padding: 2px;
}}

/* ── controls ─────────────────────────────────────────────────────── */
QPushButton {{
    background: {PANEL_BG};
    color: {CREAM};
    border: 1px solid {CONTROL_BORDER};
    padding: 4px 10px;
}}
QPushButton:hover  {{ border-color: {AMBER}; color: {AMBER}; }}
QPushButton:pressed {{ background: {SELECTED}; }}
QPushButton:disabled {{ color: {FAINT}; border-color: {PANEL_BORDER}; }}
QPushButton[chip="true"] {{
    font-family: {f['mono']};
    font-size: {CAPTION_PX}px;
    padding: 2px 7px;
}}
QPushButton[toggled="true"] {{
    background: {SELECTED};
    border-color: {AMBER};
    color: {AMBER};
}}

QLineEdit, QPlainTextEdit, QDateEdit {{
    background: {BG};
    color: {CREAM};
    border: 1px solid {CONTROL_BORDER};
    padding: 3px 6px;
    selection-background-color: {SELECTED};
}}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {AMBER}; }}

QSlider::groove:horizontal {{
    background: {BG};
    border: 1px solid {PANEL_BORDER};
    height: 4px;
}}
QSlider::sub-page:horizontal {{ background: {AMBER}; }}
QSlider::handle:horizontal {{
    background: {CREAM};
    border: 1px solid {CONTROL_BORDER};
    width: 9px;
    margin: -5px 0;
}}
QSlider::handle:horizontal:hover {{ background: {AMBER}; }}

/* ── tables ───────────────────────────────────────────────────────── */
QTableView {{
    background: {PANEL_BG};
    alternate-background-color: {PANEL_BG};
    color: {CREAM};
    border: none;
    gridline-color: transparent;
    font-family: {f['mono']};
    font-size: {MONO_PX}px;
    selection-background-color: {SELECTED};
    selection-color: {CREAM};
    outline: none;
}}
QTableView::item {{ border-bottom: 1px solid {ROW_LINE}; padding: 3px 4px; }}
QTableView::item:selected {{ background: {SELECTED}; color: {CREAM}; }}
QHeaderView::section {{
    background: {PANEL_BG};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {PANEL_BORDER};
    font-family: {f['body']};
    font-size: {CAPTION_PX}px;
    padding: 4px;
    text-align: left;
}}
QHeaderView::section:hover {{ color: {CREAM}; }}
QTableCornerButton::section {{ background: {PANEL_BG}; border: none; }}

/* ── scroll areas ─────────────────────────────────────────────────── */
QScrollArea {{ border: none; background: {BG}; }}
QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {PANEL_BORDER}; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {CONTROL_BORDER}; }}
QScrollBar:horizontal {{ background: {BG}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {PANEL_BORDER}; min-width: 24px; }}
QScrollBar::handle:horizontal:hover {{ background: {CONTROL_BORDER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ── toolbar ──────────────────────────────────────────────────────── */
QFrame#toolbar {{ background: {BG}; border-bottom: 1px solid {PANEL_BORDER}; }}
QLabel#statusChip {{
    font-family: {f['mono']};
    font-size: {CAPTION_PX}px;
    border: 1px solid {PANEL_BORDER};
    padding: 2px 7px;
    background: transparent;
}}
QLabel#statusChip[level="warn"]  {{ color: {AMBER}; border-color: {AMBER}; }}
QLabel#statusChip[level="error"] {{ color: {RED};   border-color: {RED}; }}
QLabel#statusChip[level="busy"]  {{ color: {CYAN};  border-color: {CYAN}; }}
QLabel#statusChip[level="ok"]    {{ color: {MUTED}; }}
"""

"""
Bundled fonts (gui/resources/fonts, see its README.md): registered with Qt
once at startup, and the curated list the Advanced Editor's font dropdown
offers. Kept small and varied on purpose -- every entry looks clearly
different.
"""
from __future__ import annotations

from pathlib import Path

FONT_DIR = Path(__file__).parent / "resources" / "fonts"

# (category, family) in dropdown order
FONT_CHOICES = [
    ("Comic", "Comic Neue"),
    ("Comic", "Bangers"),
    ("Comic", "Luckiest Guy"),
    ("Comic", "Patrick Hand"),
    ("Comic", "Permanent Marker"),
    ("Serif", "Tinos"),                 # Times New Roman metrics
    ("Serif", "Playfair Display"),
    ("Serif", "Cinzel"),
    ("Sans", "Roboto"),
    ("Sans", "Montserrat"),
    ("Sans", "Oswald"),
    ("Sans", "Anton"),
    ("Sans", "Bebas Neue"),
    ("Typewriter", "Courier Prime"),
    ("Typewriter", "Special Elite"),
    ("Script", "Pacifico"),
    ("Script", "Caveat"),
    ("Script", "Lobster"),
    ("Novelty", "Press Start 2P"),
    ("Novelty", "Creepster"),
    ("Novelty", "Orbitron"),
]
DISPLAY_NAMES = {"Tinos": "Tinos (Times New Roman)"}

_loaded = False


def load_bundled_fonts() -> None:
    """Register every bundled .ttf with Qt (idempotent). Needs a
    QGuiApplication."""
    global _loaded
    if _loaded:
        return
    from PySide6.QtGui import QFontDatabase
    for f in sorted(FONT_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(f))
    _loaded = True


def installed_back_issues() -> "str | None":
    """Back Issues (Blambot) if it's installed on the system -- it can't be
    bundled, but it's the preferred bubble font when present."""
    from PySide6.QtGui import QFontDatabase
    for f in QFontDatabase.families():
        if "backissue" in f.lower().replace(" ", ""):
            return f
    return None

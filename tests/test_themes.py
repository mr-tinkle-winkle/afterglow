"""Themes (gui/theme_presets.py + gui/theme_fx.py): every theme builds the
whole window, draws all of its icons, and never touches the user's own
settings; classic changes nothing.

    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_themes.py
(also runs with QT_QPA_PLATFORM=offscreen)
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="themes_home_")
os.environ["HOME"] = HOME
os.environ["XDG_CONFIG_HOME"] = str(Path(HOME) / ".config")
os.environ["XDG_DATA_HOME"] = str(Path(HOME) / ".local" / "share")
os.environ["XDG_RUNTIME_DIR"] = HOME
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPointF
from PySide6.QtGui import QEnterEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

FAILS = []


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


app = QApplication.instance() or QApplication(sys.argv)
from afterglow import config, db, library  # noqa: E402
from afterglow.gui import fonts, theme_presets as tp, theme_fx  # noqa: E402
from afterglow.gui import resources  # noqa: E402

fonts.load_bundled_fonts()
db.init_db()
clips = config.load().clips_path()
clips.mkdir(parents=True, exist_ok=True)
for i in range(3):
    f = clips / f"t{i}.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(f)], check=True)
    v = library.add_video(f, title=f"clip {i}")
    if i == 0:
        library.set_favorite(v.id, True)

from PySide6.QtGui import QFontDatabase  # noqa: E402
fams = set(QFontDatabase.families())
needed = {p.font_body for p in tp.PRESETS.values()} | {p.font_display for p in tp.PRESETS.values()} | {tp.SYM, tp.MONO, tp.INK}
check(needed <= fams, f"every theme font is bundled and registered (missing: {sorted(needed - fams)})")

check(len(tp.PRESETS) == 10 and tp.ORDER[0] == "classic" and set(tp.ORDER[1:]) == set(tp.PRESETS),
      "10 themes + classic, all listed in Settings order")
check({k for k, p in tp.PRESETS.items() if p.dark} == {"scrapbook", "aero"}, "Scrapbook and Aero have Dark mode")

user_before = config.load().appearance.afterglow_color_accent
from afterglow.gui.main_window import MainWindow  # noqa: E402

variants = [(k, False) for k in tp.PRESETS] + [("scrapbook", True), ("aero", True)]
for tid, dark in variants:
    label = tid + (" (dark)" if dark else "")
    st = config.load()
    st.appearance.theme, st.appearance.theme_dark = tid, dark
    config.save(st)
    tp.set_active_for_tests(tid, dark)
    p = tp.active()
    ok_icons = all(not resources.resource_qpixmap(n).isNull() and resources.resource_qpixmap(n).width() == tp.GLYPH_PX
                   for n in tp.ICON_NAMES)
    # each glyph actually draws something
    inked = True
    for n in tp.ICON_NAMES:
        img = resources.resource_qpixmap(n).toImage()
        if not any(img.pixelColor(x, y).alpha() > 0 for x in range(0, img.width(), 8) for y in range(0, img.height(), 8)):
            inked = False
            print("   empty glyph:", n)
    check(ok_icons and inked, f"{label}: all {len(tp.ICON_NAMES)} icons are the theme's own glyphs, none empty")
    ro, raw = config.load_readonly().appearance, config.load().appearance
    check(ro.afterglow_color_accent == p.accent[:7] and raw.afterglow_color_accent == user_before,
          f"{label}: drawing code sees the theme's colours; the user's own settings are untouched")
    w = MainWindow()
    w.resize(1400, 900)
    w.show()
    QTest.qWait(1500)
    cards = w.library_page.local_tab._cards
    check(len(cards) == 3 and all(c._theme_fx is not None for c in cards), f"{label}: cards built, each with its effects overlay")
    check(w.library_page.theme_header is not None and w.library_nav_btn._theme_word == p.words["nav_library"],
          f"{label}: header title and the sidebar's words")
    if cards:
        c = cards[0]
        QApplication.sendEvent(c, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
        QTest.qWait(300)
        img = w.grab().toImage()
        check(not img.isNull() and img.width() > 100, f"{label}: hover + paint without errors")
    img = QImage(400, 300, QImage.Format_ARGB32)
    from PySide6.QtGui import QPainter
    from PySide6.QtCore import QRectF
    g = QPainter(img)
    theme_fx.paint_backdrop(g, QRectF(0, 0, 400, 300), p, t=1.5)
    g.end()
    check(img.pixelColor(200, 150).alpha() == 255, f"{label}: backdrop paints ({p.backdrop})")
    w.close()
    w.deleteLater()
    QTest.qWait(50)

# classic: nothing changes
st = config.load()
st.appearance.theme, st.appearance.theme_dark = "classic", False
config.save(st)
tp.set_active_for_tests("classic")
check(tp.active() is None and tp.word("tab_local", "Local") == "Local", "classic: no theme, default words")
check(tp.glyph_pixmap("library.png") is None and not resources.resource_qpixmap("library.png").isNull(), "classic: the bundled PNG icons")
check(config.load_readonly().appearance.afterglow_color_accent == user_before, "classic: the user's own colours")
w = MainWindow()
w.show()
QTest.qWait(1200)
check(w.library_page.theme_header is None and all(c._theme_fx is None for c in w.library_page.local_tab._cards),
      "classic: no header title, no overlays")

# animations off: nothing is scheduled
st = config.load()
st.appearance.theme, st.appearance.theme_animations = "observatory", False
config.save(st)
tp.set_active_for_tests("observatory")
w2 = MainWindow()
w2.show()
QTest.qWait(800)
check(w2.library_page.local_tab._backdrop.driver.now() is None, "Theme Animations off: the backdrop stands still")

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)

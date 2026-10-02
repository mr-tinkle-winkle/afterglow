"""
The themed message / confirm dialog (the video Delete prompt, "Replace file?"): its text is never cut
off, however long the message or an unbroken clip title is.

    QT_QPA_PLATFORM=offscreen python3 tests/test_message_dialog.py
"""
import os, sys, tempfile
os.environ["HOME"] = tempfile.mkdtemp(prefix="msgdlg_home_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PySide6.QtWidgets import QApplication, QLabel
app = QApplication([])
from afterglow import db
from afterglow.gui.custom_message_dialog import CustomMessageDialog, DIALOG_WIDTH, _breakable

db.init_db()
FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        FAILS.append(m)

TAIL = "? This removes the file from disk and can't be undone."
cases = {
    "short title": "Delete 'x'" + TAIL,
    "long title with spaces": "Delete '" + "Ace clutch on Ascent round twelve " * 4 + "'" + TAIL,
    "unbroken title": "Delete '" + "clip_2026-10-01_23-11-45_" * 5 + "'" + TAIL,
    "markup in a title": "Delete '<b>bold</b> & <i>it</i>'" + TAIL,
    "several lines": "Replace this file?\n" + "\n".join(f"/some/long/path/number/{i}/file.mp4" for i in range(6)),
}
for name, text in cases.items():
    d = CustomMessageDialog("Delete Video", text, confirm_label="Delete", danger=True)
    d.show(); app.processEvents()
    lab = d.text_label
    need = lab.heightForWidth(lab.width())
    check(lab.height() >= need, f"{name}: the text label is tall enough ({lab.height()} >= {need})")
    check(d.height() >= d.layout().heightForWidth(d.width()) - 1, f"{name}: the dialog is tall enough for its content at its width")
    check(d.width() == DIALOG_WIDTH, f"{name}: fixed width")
    check(lab.sizeHint().width() <= d.width() or lab.wordWrap(), f"{name}: no horizontal clipping")
    if "markup" in name:
        check("<b>" in lab.text() and lab.textFormat().name == "PlainText", "markup in a title is shown as text")
    ok = d.ok_button
    check(d.rect().contains(ok.geometry()), f"{name}: the Delete button is inside the dialog")
    d.close()

check(_breakable("a" * 70).count("​") == 2 and _breakable("a b c") == "a b c", "long tokens get break points, normal text is untouched")

print()
print("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: " + "; ".join(FAILS))
sys.exit(1 if FAILS else 0)

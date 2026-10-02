"""
No native/KDE dialogs or widgets left: the message, text, colour and file
dialogs are the app's own; combo boxes draw themselves and open their own
popup; tooltips, text-field menus and stray scroll bars are app-styled.

    QT_QPA_PLATFORM=offscreen python3 tests/test_themed_dialogs.py
"""
import os, re, sys, tempfile
from pathlib import Path
HOME = tempfile.mkdtemp(prefix="themed_home_")
os.environ["HOME"] = HOME
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import Qt, QTimer, QPoint, QEvent
from PySide6.QtGui import QColor, QFont, QHelpEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QPlainTextEdit, QLineEdit, QWidget, QLabel
app = QApplication([])
from afterglow import config, db
db.init_db()

FAILS = []
def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        FAILS.append(m)

# ---- 1. static scan ------------------------------------------------------
banned = {
    r"\bQMessageBox\b": "QMessageBox", r"\bQInputDialog\b": "QInputDialog",
    r"\bQColorDialog\b": "QColorDialog", r"\bQFileDialog\b": "QFileDialog",
    r"\bQDialogButtonBox\b": "QDialogButtonBox", r"\bQComboBox\(\)": "raw QComboBox()",
    r"QFrame\.StyledPanel": "StyledPanel frame", r"\bQFontDialog\b": "QFontDialog",
    r"\bQProgressDialog\b": "QProgressDialog", r"\bQErrorMessage\b": "QErrorMessage",
}
hits = []
for f in (ROOT / "afterglow").rglob("*.py"):
    if f.name in ("themed_dialogs.py", "custom_message_dialog.py", "themed_frame.py"):
        continue
    in_doc = False
    for n, line in enumerate(f.read_text().splitlines(), 1):
        code = line.split("#", 1)[0]
        quotes = line.count('"""')
        if in_doc or quotes:
            if quotes % 2:
                in_doc = not in_doc
            continue
        for pat, name in banned.items():
            if re.search(pat, code):
                hits.append(f"{f.relative_to(ROOT)}:{n} {name}")
check(not hits, "no native dialogs / raw combo boxes / StyledPanel frames in the app code" +
      ("" if not hits else ": " + "; ".join(hits[:8])))

from afterglow.gui import app_chrome
app_chrome.install()
from afterglow.gui.custom_message_dialog import ask_confirm, show_message, CustomMessageDialog
from afterglow.gui.themed_dialogs import (
    ask_text, get_text, get_color, ColorPickerDialog, FilePickerDialog, parse_filters,
    get_open_file_name, get_save_file_name, get_existing_directory, get_open_file_names, ThemedDialog,
)
from afterglow.gui.custom_combo_box import CustomComboBox
from afterglow.gui.custom_scrollbar import CustomScrollBar


def later(fn, ms=30):
    QTimer.singleShot(ms, fn)


def modal():
    return QApplication.activeModalWidget()

# ---- 2. message / confirm / text ----------------------------------------
def click_named(label):
    def go():
        from afterglow.gui.custom_button import CustomButton
        w = modal()
        for b in w.findChildren(CustomButton):
            if b.text() == label:
                b.click()
                return
    return go

later(click_named("Delete"))
check(ask_confirm(None, "Delete Video", "Delete it?", "Delete", danger=True) is True, "ask_confirm: Delete -> True")
later(click_named("Cancel"))
check(ask_confirm(None, "Delete Video", "Delete it?", "Delete", danger=True) is False, "ask_confirm: Cancel -> False")
seen = {}
def esc_check():
    w = modal()
    seen["themed"] = isinstance(w, ThemedDialog) and bool(w.windowFlags() & Qt.FramelessWindowHint)
    seen["focus_cancel"] = w.focusWidget() is not None and w.focusWidget().text() == "Cancel"
    QTest.keyClick(w, Qt.Key_Return)   # Enter on a danger prompt = Cancel (the focused button)
later(esc_check)
check(ask_confirm(None, "Delete Videos", "Delete 3?", "Delete", danger=True) is False,
      "Enter on a destructive confirm doesn't delete")
check(seen.get("themed"), "confirm is the frameless themed dialog")
later(click_named("OK"))
show_message(None, "Copy Failed", "nope")
check(True, "show_message opens and closes")

def type_text():
    w = modal()
    w.edit.setText("  New Name  ")
    QTest.keyClick(w.edit, Qt.Key_Return)
later(type_text)
check(ask_text(None, "Rename", "Name:", "old") == "New Name", "ask_text returns the stripped text")
def type_text2():
    w = modal()
    w.edit.setText("Cat")
    click_named("OK")()
later(type_text2)
check(get_text(None, "New Category", "Category name:") == ("Cat", True), "get_text -> (text, True)")
later(click_named("Cancel"))
check(get_text(None, "New Category", "Category name:") == ("", False), "get_text cancelled -> ('', False)")

# The video card's delete prompt goes through ask_confirm.
from afterglow.gui import video_card
src = Path(video_card.__file__).read_text()
check('ask_confirm(self, "Delete Video"' in src and "danger=True" in src, "video card delete uses the themed confirm")

# ---- 3. colour picker ----------------------------------------------------
dlg = ColorPickerDialog(QColor("#336699"), None, "Choose Color")
check(dlg.hex_edit.text() == "#336699", "picker starts at the initial colour")
dlg.hex_edit.setText("#f00")
dlg._hex_entered()
check(dlg.current_color().name() == "#ff0000", "short hex accepted")
dlg.hex_edit.setText("zzz")
dlg._hex_entered()
check(dlg.hex_edit.text() == "#ff0000", "bad hex is put back")
dlg.show(); app.processEvents()
sq = dlg.square
QTest.mouseClick(sq, Qt.LeftButton, Qt.NoModifier, QPoint(sq.width() - 2, 1))
check(dlg.current_color().saturation() > 240 and dlg.current_color().value() > 240, "click in the square sets S/V")
QTest.mouseClick(dlg.hue, Qt.LeftButton, Qt.NoModifier, QPoint(dlg.hue.width() // 3 + 2, 9))
check(abs(dlg.current_color().hueF() - 0.333) < 0.06, "hue strip sets the hue")
dlg.close()
dlg = ColorPickerDialog(QColor(10, 20, 30, 128), None, "Text color", alpha=True)
check(dlg.hex_edit.text() == "#0a141e80", "alpha picker shows #rrggbbaa")
dlg.hex_edit.setText("#ff000040")
dlg._hex_entered()
check(dlg.current_color().alpha() == 0x40, "alpha hex accepted")
def pick_swatch():
    w = modal()
    from afterglow.gui.themed_dialogs import _Swatch
    sw = [s for s in w.findChildren(_Swatch) if s.color.name() == "#34c759"][0]
    QTest.mouseClick(sw, Qt.LeftButton)
    click_named("Select")()
later(pick_swatch)
c = get_color(QColor("#000000"), None, "Choose Color")
check(c.isValid() and c.name() == "#34c759", "swatch + Select returns that colour")
later(click_named("Cancel"))
check(not get_color(QColor("#000000"), None).isValid(), "cancel returns an invalid colour (like QColorDialog)")
check((config.CONFIG_DIR / "recent_colors.json").exists(), "picked colours are kept as recents")

# ---- 4. file picker ------------------------------------------------------
root = Path(tempfile.mkdtemp(prefix="picker_"))
(root / "sub").mkdir()
(root / "a.wav").write_bytes(b"x")
(root / "b.txt").write_text("x")
(root / ".hidden.wav").write_bytes(b"x")
(root / "sub" / "c.mp3").write_bytes(b"x")
check(parse_filters("Audio Files (*.wav *.mp3);;All Files (*)")[0][1] == ["*.wav", "*.mp3"], "filter parsing")

d = FilePickerDialog(None, "Choose Sound", str(root), "Audio Files (*.wav *.mp3 *.ogg);;All Files (*)", "open")
names = [d.list.item(i).text() for i in range(d.list.count())]
check(names == ["sub", "a.wav"], f"audio filter: folders first, only matching files, no hidden ({names})")
d.filter_combo.setCurrentIndex(1)
names = [d.list.item(i).text() for i in range(d.list.count())]
check(names == ["sub", "a.wav", "b.txt"], "All Files filter shows everything")
d.hidden_box.setChecked(True)
check(d.list.count() == 4, "show hidden files")
d.navigate(str(root / "sub"))
check(d.cwd == str((root / "sub").resolve()) and d.list.item(0).text() == "c.mp3", "navigate into a folder")
d.go_back()
check(d.cwd == str(root.resolve()), "back")
d.close()

def open_one():
    w = modal()
    for i in range(w.list.count()):
        if w.list.item(i).text() == "a.wav":
            w.list.setCurrentRow(i)
    click_named("Open")()
later(open_one)
path, filt = get_open_file_name(None, "Choose Sound", str(root), "Audio Files (*.wav *.mp3);;All Files (*)")
check(path == str((root / "a.wav").resolve()) and filt.startswith("Audio"), f"open returns the path + filter ({path})")

def open_two():
    w = modal()
    w.filter_combo.setCurrentIndex(1)
    w.name_edit.setText('"a.wav" "b.txt"')
    click_named("Open")()
later(open_two)
paths, _ = get_open_file_names(None, "Add", str(root), "Audio (*.wav);;All Files (*)")
check([Path(x).name for x in paths] == ["a.wav", "b.txt"], "open many")

def save_new():
    w = modal()
    w.name_edit.setText("exported")
    click_named("Save")()
later(save_new)
out, _ = get_save_file_name(None, "Export Settings", str(root / "afterglow-settings.toml"), "TOML Files (*.toml)")
check(out == str(root.resolve() / "exported.toml"), f"save adds the filter's extension ({out})")

state = {}
def save_existing():
    w = modal()
    w.name_edit.setText("a.wav")
    def confirm_replace():
        state["asked"] = isinstance(modal(), CustomMessageDialog)
        click_named("Replace")()
    later(confirm_replace, 20)
    click_named("Save")()
later(save_existing)
out, _ = get_save_file_name(None, "Save", str(root), "Audio (*.wav)")
check(state.get("asked") and out.endswith("a.wav"), "saving over a file asks first (themed)")

def choose_dir():
    w = modal()
    for i in range(w.list.count()):
        if w.list.item(i).text() == "sub":
            w.list.setCurrentRow(i)
    state["dir_files"] = [w.list.item(i).text() for i in range(w.list.count())]
    click_named("Choose Folder")()
later(choose_dir)
got = get_existing_directory(None, "Choose Clips Folder", str(root))
check(got == str((root / "sub").resolve()), f"choose folder ({got})")
check(state["dir_files"] == ["sub"], "folder mode lists folders only")
later(click_named("Cancel"))
check(get_open_file_name(None, "x", str(root), "") == ("", ""), "cancel -> ('', '')")
d = FilePickerDialog(None, "", "afterglow-settings.toml", "TOML Files (*.toml)", "save")
check(d.name_edit.text() == "afterglow-settings.toml" and Path(d.cwd).is_dir(),
      "a bare suggested name lands in a real folder, not the process cwd")
d.close()

# ---- 5. combo box --------------------------------------------------------
host = QWidget(); host.resize(400, 300)
c = CustomComboBox(host)
c.addItems(["One", "Two"])
c.insertSeparator(c.count())
c.addItem("Three", "three-data")
c.setItemData(3, QFont("Serif"), Qt.FontRole)
c.move(20, 20); c.resize(160, 30)
host.show(); app.processEvents()
activated = []
c.activated.connect(activated.append)
c.showPopup(); app.processEvents()
pop = c.popup_widget()
check(pop is not None and pop.isVisible(), "combo opens its own popup")
check(not (pop.list.item(2).flags() & Qt.ItemIsEnabled), "separator row isn't pickable")
check(pop.list.item(3).data(Qt.FontRole) is not None, "per-row font carried into the popup")
r = pop.list.visualItemRect(pop.list.item(3))
QTest.mouseClick(pop.list.viewport(), Qt.LeftButton, Qt.NoModifier, r.center())
app.processEvents()
check(c.currentIndex() == 3 and c.currentData() == "three-data" and activated == [3],
      "clicking a row picks it and emits activated")
check(c.popup_widget() is None, "popup closes after picking")
c.showPopup(); app.processEvents()
QTest.keyClick(c.popup_widget().list, Qt.Key_Escape); app.processEvents()
check(c.popup_widget() is None and c.currentIndex() == 3, "Escape closes without changing")
e = CustomComboBox(host); e.setEditable(True); e.addItems(["firefox", "obs"])
check(e.lineEdit() is not None and "transparent" in e.lineEdit().styleSheet(), "editable combo keeps a themed line edit")
img = c.grab()
check(not img.isNull(), "combo paints")

# ---- 6. app chrome -------------------------------------------------------
m = QMenu()
m.addAction("x")
m.ensurePolished()
check("QMenu" in m.styleSheet(), "a stray QMenu gets the app menu style")
le = QLineEdit(host)
cm = le.createStandardContextMenu()
cm.ensurePolished()
check("QMenu" in cm.styleSheet(), "the text-field Cut/Copy/Paste menu is styled")
pt = QPlainTextEdit(host)
pt.ensurePolished()
check(isinstance(pt.verticalScrollBar(), CustomScrollBar), "plain text edits get the custom scroll bar")
lab = QLabel("hover me", host)
lab.setToolTip("A themed tip")
lab.move(50, 200); lab.show(); app.processEvents()
ev = QHelpEvent(QEvent.ToolTip, QPoint(5, 5), lab.mapToGlobal(QPoint(5, 5)))
QApplication.sendEvent(lab, ev)
b = app_chrome._filter.bubble
check(b is not None and b.isVisible() and b.label.text() == "A themed tip", "tooltips use the themed bubble")
QTest.mouseClick(host, Qt.LeftButton)
check(not b.isVisible(), "the bubble hides on click")

print("\n%d failure(s)" % len(FAILS))
sys.exit(1 if FAILS else 0)

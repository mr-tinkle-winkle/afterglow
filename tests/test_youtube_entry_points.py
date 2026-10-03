"""
YouTube upload entry points and the process split: the main GUI (with its app-wide event filters,
which crash QtWebEngine -- see youtube/host.py) never loads QtWebEngine; everything web runs in a
real afterglow-youtube host process started on demand.  Covers: the previewer's Upload button
(and its states), the Advanced Editor's "Export & Upload", the Library's Upload action opening ONE
quick dialog for a multi-selection, a full upload driven from the GUI through the host (fake
Studio), the sign-in window and the embed player opened by the host, the red circle's click
reaching the host, and the host carrying on after the main window closes.

    DISPLAY=:99 QT_QPA_PLATFORM=xcb QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/test_youtube_entry_points.py
"""
import os
import subprocess
import sys
import tempfile
import time

HOME = tempfile.mkdtemp(prefix="yt_entry_home_")
os.environ["HOME"] = HOME
os.environ["XDG_DATA_HOME"] = os.path.join(HOME, ".local", "share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="yt_entry_run_")
if os.geteuid() == 0:
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import yt_fixtures as F  # noqa: E402

INFO = F.start()

from afterglow import db, config, library  # noqa: E402
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402
app = QApplication([])

fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


def pump(sec):
    end = time.perf_counter() + sec
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


def wait(cond, timeout=30.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def make_clip(name):
    p = CLIPS / name
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=30",
                    "-c:v", "libx264", "-preset", "ultrafast", str(p)], check=True)
    return library.add_video(p, p.stem)


va, vb, vc = make_clip("One.mp4"), make_clip("Two.mp4"), make_clip("Three.mp4")

from afterglow.gui import upload_dialog as UD, upload_queue as UQ  # noqa: E402
opened = []


class _FakeDialog:
    def __init__(self, videos, parent=None):
        opened.append([v.id for v in videos])
        self.fallback_chosen = False

    def exec(self):
        return QDialog.Rejected


UD._RealUploadDialog = UD.UploadDialog
UD.UploadDialog = _FakeDialog

# ------------------------------------------------------------------ MainWindow: socket, Library Upload action
from afterglow.gui.main_window import MainWindow  # noqa: E402
w = MainWindow()
w.resize(1600, 900)
w.show()
pump(0.8)
check(w._youtube_sync is not None and w._youtube_sync._on_change in library.metadata_listeners,
      "...and asks 'Also update it on YouTube?' after edits")
tab = w.library_page.local_tab
tab._do_refresh()
pump(0.3)
cards = {c.video_id: c for c in tab._cards}
cards[va.id].upload_many_requested.emit([va.id, vb.id])
pump(0.2)
check(opened == [[va.id, vb.id]], f"Library Upload on a multi-selection: ONE dialog for all of them ({opened})")

# ------------------------------------------------------------------ the previewer's Upload button
opened.clear()
w._show_preview_overlay(library.get_video(vc.id), None)
pump(1.0)
content = w._preview_overlay.content
check(content.upload_btn.text() == "Upload" and content.upload_btn.isVisible(), "the previewer has an Upload button")
content.upload_btn.click()
pump(0.3)
check(opened == [[vc.id]], "...which opens the quick dialog for that clip")
library.mark_upload_queued(vc.id, "t", "", "", "unlisted")
library.mark_upload_state(vc.id, "failed", error="x")
content._video = library.get_video(vc.id)
content._update_upload_button()
check(content.upload_btn.text() == "Retry Upload", "a failed upload: Retry Upload")
library.mark_upload_done(vc.id, "PREVvid1234")
content._video = library.get_video(vc.id)
content._update_upload_button()
check(content.upload_btn.text() == "Copy YouTube Link", "on YouTube: Copy YouTube Link")
content.upload_btn.click()
pump(0.2)
check(QApplication.clipboard().text() == "https://youtu.be/PREVvid1234", "...copies the link")
w._preview_overlay.close_overlay(immediate=True)
pump(0.3)

# ------------------------------------------------------------------ Advanced Editor: Export & Upload
from afterglow.gui.advanced_editor import page as page_mod  # noqa: E402
from afterglow.nle.model import default_project  # noqa: E402
proj = default_project()
dlg = page_mod.SaveDialog(proj, "", None, library_clip=True)
labels = [b.text() for b in dlg.findChildren(page_mod.CustomButton)]
check("Export & Upload" in labels, f"the export dialog offers Export & Upload for a library clip ({labels})")
btn = next(b for b in dlg.findChildren(page_mod.CustomButton) if b.text() == "Export & Upload")
btn.click()
check(dlg.result() == QDialog.Accepted and dlg.upload_after, "...which exports and flags the upload")
dlg2 = page_mod.SaveDialog(proj, "Writes x.mp4", None, library_clip=False)
check("Export & Upload" not in [b.text() for b in dlg2.findChildren(page_mod.CustomButton)],
      "an imported file's export (not a library clip) has no upload")

# ------------------------------------------------------------------ the host process: an upload from the GUI
os.environ["AFTERGLOW_YT_VERIFY_INTERVAL"] = "0.4"
os.environ["AFTERGLOW_TRASH_NO_GIO"] = "1"
s_ = config.load()
s_.youtube.step_pause_ms = 30
config.save(s_)
F.set_flow_timeouts(INFO, "upload", "vid=HOSTvid1234")
F.READY.add("HOSTvid1234")
UD.UploadDialog = UD.__dict__.get("_RealUploadDialog", UD.UploadDialog)
q = UQ.upload_queue()
from afterglow.gui.youtube_client import RemoteUploadQueue  # noqa: E402
check(isinstance(q, RemoteUploadQueue), "in the GUI the queue is the host client")
finished = []
q.job_finished.connect(finished.append)
opened_ev = []
q.opened.connect(opened_ev.append)
reqs = [UD.defaults_for(library.get_video(va.id))]
q.enqueue_uploads(reqs)
check(wait(lambda: q.connected(), 20), "the afterglow-youtube host was started on demand and connected")
check(wait(lambda: any(j.video_id == va.id and j.state == "running" for j in q.jobs), 20),
      "job snapshots stream back from the host")
check(wait(lambda: any(j.video_id == va.id and j.finished for j in finished), 90), "the upload finishes in the host")
v = library.get_video(va.id)
check(v.is_uploaded and v.youtube_video_id == "HOSTvid1234" and v.local_deleted, "recorded in the shared library")
check("PySide6.QtWebEngineWidgets" not in sys.modules and "PySide6.QtWebEngineCore" not in sys.modules,
      "the GUI process never loaded QtWebEngine")

# the red circle's click (from the indicator helper) reaches the host
from afterglow.youtube import gui_socket  # noqa: E402
import threading  # noqa: E402
threading.Thread(target=lambda: gui_socket.send({"event": "show_upload", "id": "nothing"}), daemon=True).start()
check(wait(lambda: "show_upload" in opened_ev, 10), "the red circle's click (indicator helper -> socket) reaches the host")

# the sign-in window, in the host
names = []
q.signed_in.connect(names.append)
w.settings_page.youtube_page._sign_in()
check(wait(lambda: bool(names), 40), "Settings > Sign in opens the window in the host, which notices Studio opened")
check(names and names[0] == "Fake Channel Name" and config.load().youtube.account_name == "Fake Channel Name",
      "...the channel name is saved")
pump(0.3)
check(w.settings_page.youtube_page.account_label.text() == "Signed in: Fake Channel Name", "...and shown in Settings")

# the embed player, in the host
opened_ev.clear()
q.play(va.id)
check(wait(lambda: "play" in opened_ev, 20), "double-click's player opens in the host")
check(w.isVisible(), "the GUI is unaffected (no web view in this process)")

# closing the main window mid-upload: the host carries on
F.set_flow_timeouts(INFO, "upload", "slow=1&vid=LATEvid1234")
q.enqueue_uploads([UD.defaults_for(library.get_video(vb.id))])
check(wait(lambda: q.busy(), 20), "a second upload runs")
notes = []
import afterglow.gui.main_window as MW  # noqa: E402
MW._notify = lambda t: notes.append(t)
w.close()
pump(0.3)
check(notes and "background" in notes[0], "closing the window mid-upload: a one-line notice")
F.READY.add("LATEvid1234")
check(wait(lambda: library.get_video(vb.id).is_uploaded, 90), "...and the host finishes the upload without the window")
q._send({"cmd": "quit"})
pump(0.5)

print()
print(f"{len(fails)} failure(s)" if fails else "ALL PASS")
sys.exit(1 if fails else 0)

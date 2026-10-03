"""
YouTube upload end to end through the GUI pieces, against the local fake Studio / oEmbed /
thumbnail server (tests/yt_fixtures.py): the quick dialog's prefilled values, the queue running an
upload hidden, verification waiting for the video to really play, the local file going to the
TRASH, the Uploaded tab, the red indicator circle and the sounds; a failure (window pops out, file
untouched) then Retry; "I finished it here"; delete-local off (both tabs); a batch running one at a
time; cancelling a queued upload; the browser fallback's registration; renaming an uploaded clip
-> "Also update it on YouTube?" -> the edit flow; Delete from YouTube; Remove from afterglow;
recovery of uploads a crash interrupted; the Library cards (badge, YouTube thumbnail, double-click
plays the embed); the red circle's click reaching the GUI socket; Settings > YouTube.

    DISPLAY=:99 QT_QPA_PLATFORM=xcb QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/test_youtube_queue.py
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="yt_queue_home_")
os.environ["HOME"] = HOME
os.environ["XDG_DATA_HOME"] = os.path.join(HOME, ".local", "share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="yt_queue_run_")
os.environ["AFTERGLOW_TRASH_NO_GIO"] = "1"          # the spec trash, inside this test's HOME
os.environ["AFTERGLOW_YT_VERIFY_INTERVAL"] = "0.4"
os.environ["AFTERGLOW_YT_VERIFY_TIMEOUT"] = "25"
if os.geteuid() == 0:
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import yt_fixtures as F  # noqa: E402

INFO = F.start()

from afterglow import db, config, library, clips, indicator_client  # noqa: E402
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)
s = config.load()
s.youtube.step_pause_ms = 30
s.youtube.delete_local_after_upload = True
s.youtube.upload_done_sound = "/done.wav"
s.youtube.upload_error_sound = "/error.wav"
s.youtube.account_name = "Fake Channel"
config.save(s)

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication([])

SOUNDS, EVENTS = [], []
clips.play_sound = lambda p: SOUNDS.append(p)
indicator_client.set_test_sink(EVENTS.append)

from afterglow.gui import upload_queue as UQ, custom_message_dialog as CMD  # noqa: E402
from afterglow.gui.upload_dialog import UploadDialog, defaults_for, FallbackUploadDialog  # noqa: E402
from afterglow.youtube import trash  # noqa: E402

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


def wait(cond, timeout=60.0):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def make_clip(name, tags=(), cfg_id=None):
    p = CLIPS / name
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x180:rate=30",
                    "-c:v", "libx264", "-preset", "ultrafast", str(p)], check=True)
    v = library.add_video(p, p.stem, clip_config_id=cfg_id)
    for t in tags:
        library.add_tag_to_video(v.id, t)
    return library.get_video(v.id)


cfg = clips.create_clip_config("Valorant", 30, youtube_playlist="Valorant Clips")
va = make_clip("Ace on B.mp4", ["Clutch", "1v3"], cfg.id)
q = UQ.UploadQueue()
UQ.set_upload_queue(q)

# ------------------------------------------------------------------ the quick dialog
d = defaults_for(va)
check(d["title"] == "Ace on B" and d["playlist"] == "Valorant Clips" and d["privacy"] == "unlisted",
      "prefilled: title = clip title, playlist from the clip type, visibility unlisted")
check(d["description"].startswith("#1v3 #Clutch\n\nValorant -- captured") and "Length 0:02" in d["description"],
      f"prefilled description from the template ({d['description']!r})")
dlg = UploadDialog([va])
row = dlg.rows[0]
check(row.title_edit.text() == "Ace on B" and row.playlist_edit.text() == "Valorant Clips"
      and row.privacy_combo.currentData() == "unlisted" and row.desc_edit.toPlainText() == d["description"],
      "the dialog shows those values, editable")
row.title_edit.setText("Ace <on> B")
check("replaced" in row.problem_label.text(), "'<' '>' are flagged (and replaced on upload)")
check(dlg.requests()[0]["title"] == "Ace ‹on› B", "...replaced in what is sent")
row.title_edit.setText("")
check(row.check() and "empty" in row.check()[0], "an empty title blocks the upload")
row.title_edit.setText("Ace on B")
vb_ = make_clip("Batch One.mp4")
vc_ = make_clip("Batch Two.mp4")
bdlg = UploadDialog([vb_, vc_])
check(len(bdlg.rows) == 2 and bdlg.windowTitle() == "Upload 2 clips to YouTube", "batch: ONE dialog, a row per clip")
bdlg.all_privacy.setCurrentIndex(bdlg.all_privacy.findData("public"))
check([r.values()["privacy"] for r in bdlg.rows] == ["public", "public"], "batch: visibility for all")

# ------------------------------------------------------------------ a full upload
F.set_flow_timeouts(INFO, "upload", "slow=1")
EVENTS.clear()
job = q.enqueue_uploads(dlg.requests())[0]
check(library.get_video(va.id).upload_state == "queued", "queued in the library")
check(wait(lambda: job.state == "running", 10), "the job starts")
w = q.window()
check(w.isVisible() and not w.is_on_screen(), "it runs in the hidden Studio window")
check(wait(lambda: library.get_video(va.id).youtube_pending_id == "FAKEvid1234", 30), "the link is recorded while uploading")
check(wait(lambda: job.state == "verifying", 60), "Studio done -> verifying")
check(library.get_video(va.id).upload_state == "processing" and Path(va.path).exists(),
      "...processing; nothing deleted while YouTube hasn't confirmed")
pump(1.5)
check(job.state == "verifying" and not library.get_video(va.id).is_uploaded, "oEmbed not answering yet: still waiting")
F.READY.add("FAKEvid1234")
check(wait(lambda: job.state == "done", 30), "oEmbed answers -> done")
v = library.get_video(va.id)
check(v.is_uploaded and v.youtube_video_id == "FAKEvid1234" and v.youtube_title == "Ace on B"
      and v.youtube_playlist == "Valorant Clips", "recorded: id, title, playlist")
check(v.thumb_cache_path and v.thumb_cache_path.endswith("FAKEvid1234.jpg") and Path(v.thumb_cache_path).stat().st_size > 2500,
      "YouTube's thumbnail cached for the Uploaded tab")
check(v.local_deleted and not Path(va.path).exists() and (trash.home_trash() / "files" / "Ace on B.mp4").exists(),
      "the local file went to the system TRASH (recoverable), not erased")
ids_up = [x.id for x in library.list_videos(uploaded_only=True)]
ids_local = [x.id for x in library.list_videos(local_only=True)]
check(va.id in ids_up and va.id not in ids_local, "Uploaded tab only now")
evs = [e["event"] for e in EVENTS]
check(evs and evs[0] == "upload_start" and evs[-1] == "upload_done" and EVENTS[0]["style"]["kind"] == "upload",
      f"the red circle: upload_start ... upload_done ({evs[:3]}...{evs[-2:]})")
check(SOUNDS == ["/done.wav"], f"the upload-done sound played ({SOUNDS})")
check(wait(lambda: not w.isVisible(), 5), "the Studio window is put away once nothing runs")
check(not q.busy(), "the queue is idle")

# ------------------------------------------------------------------ a failure, then Retry
F.set_flow_timeouts(INFO, "upload", "fail=picker&vid=SECONDvid12", file_picker=1500)
SOUNDS.clear()
EVENTS.clear()
vb = make_clip("Second.mp4")
job = q.enqueue_uploads([defaults_for(vb)])[0]
check(wait(lambda: job.state == "waiting_user", 30), "a step fails -> waiting for the user")
check(w.is_on_screen(), "the Studio window pops out where it stopped")
vis = [k for k, b in w.buttons.items() if b.isVisibleTo(w)]
check(set(vis) == {"retry", "finished", "cancel", "hide"} and "file_picker" in w.message.text(),
      f"the banner names the step and offers Retry / I finished it here / Cancel ({vis})")
check(library.get_video(vb.id).upload_state == "failed" and Path(vb.path).exists(), "failed in the library; file untouched")
check([e["event"] for e in EVENTS][-1] == "upload_fail" and SOUNDS == ["/error.wav"], "the circle shakes; the error sound")
check(q.current is job, "the queue waits for the answer (the page is the upload)")
F.set_flow_timeouts(INFO, "upload", "vid=SECONDvid12", file_picker=45000)
F.READY.add("SECONDvid12")
w.action.emit("retry")
check(wait(lambda: job.state == "done", 60), "Retry -> uploaded")
check(library.get_video(vb.id).youtube_video_id == "SECONDvid12" and not Path(vb.path).exists(), "...recorded, file trashed")
check(sum(1 for e in EVENTS if e["event"] == "upload_start") == 2, "a fresh red circle for the retry")

# ------------------------------------------------------------------ "I finished it here"
F.set_flow_timeouts(INFO, "upload", "vid=THIRDvid123")
vc = make_clip("Third.mp4")
req = defaults_for(vc)
req["playlist"] = "No Such Playlist"
job = q.enqueue_uploads([req])[0]
check(wait(lambda: job.state == "waiting_user", 30) and job.step == "playlist", "fails at the playlist step")
check(job.yt_id == "THIRDvid123", "...the link was already read")
F.READY.add("THIRDvid123")
w.action.emit("finished")
check(wait(lambda: job.state == "done", 30), "I finished it here -> verified and recorded")
check(library.get_video(vc.id).is_uploaded and not w.is_on_screen(), "...and the window tucked away")

# ------------------------------------------------------------------ delete-local off + a batch, one at a time
s = config.load()
s.youtube.delete_local_after_upload = False
config.save(s)
F.set_flow_timeouts(INFO, "upload", "vid=BATCHvid123")
F.READY.add("BATCHvid123")
started = []
q.changed.connect(lambda: started.extend(j.video_id for j in q.jobs if j.state == "running" and j.video_id not in started))
jobs = q.enqueue_uploads([dict(r, title=r["title"]) for r in bdlg.requests()])
check(len(jobs) == 2 and jobs[1].state == "queued", "batch queued")
check(wait(lambda: all(j.state == "done" for j in jobs), 120), "both uploaded")
check(started.index(vb_.id) < started.index(vc_.id) if vb_.id in started and vc_.id in started else False,
      "one after another, in order")
for j in jobs:
    vv = library.get_video(j.video_id)
    check(vv.is_uploaded and Path(vv.path).exists() and vv.youtube_privacy == "public",
          f"delete-local off: '{vv.title}' kept its file, in BOTH tabs")
check(vb_.id in [x.id for x in library.list_videos(local_only=True)] and vb_.id in
      [x.id for x in library.list_videos(uploaded_only=True)], "...listed in Local and Uploaded")

# cancel a queued upload
vd = make_clip("Queued.mp4")
ve = make_clip("Queued2.mp4")
F.READY.add("FAKEvid1234")
F.set_flow_timeouts(INFO, "upload", "slow=1")
j1, j2 = q.enqueue_uploads([defaults_for(vd), defaults_for(ve)])
wait(lambda: j1.state == "running", 10)
q.cancel(ve.id)
check(j2.state == "cancelled" and library.get_video(ve.id).upload_state is None, "a queued upload can be cancelled")
check(wait(lambda: j1.finished, 90) and j1.state == "done", "...the running one carries on")

# ------------------------------------------------------------------ the browser fallback
vf = make_clip("Fallback.mp4")
fb = FallbackUploadDialog([defaults_for(vf)])
fb.link_edits[vf.id].setText("not a link")
orig_show = CMD.show_message
import afterglow.gui.upload_dialog as UD  # noqa: E402
shown = []
UD.show_message = lambda *a: shown.append(a[2])
fb._register()
check(shown and "don't look like" in shown[0] and not fb.registered, "a pasted non-link is refused")
fb.link_edits[vf.id].setText("https://youtu.be/MANUALvid12?si=abc")
fb._register()
check(fb.registered and fb.registered[0]["yt_id"] == "MANUALvid12", "a pasted youtu.be link is accepted")
F.READY.add("MANUALvid12")
mj = q.register_manual(fb.registered[0])
check(wait(lambda: mj.state == "done", 30) and library.get_video(vf.id).youtube_video_id == "MANUALvid12",
      "registered + verified like any upload")

# ------------------------------------------------------------------ rename an uploaded clip -> update on YouTube
from afterglow.gui.youtube_sync import YouTubeSync  # noqa: E402
asked = []
CMD.ask_confirm = lambda parent, title, text, label, danger=False: (asked.append(text), True)[1]
sync = YouTubeSync()
library.rename_video(vb.id, title="Second, renamed")
library.add_tag_to_video(vb.id, "Ace")
pump(0.3)
check(not asked, "not asked mid-edit (debounced)")
check(wait(lambda: bool(asked), 5) and len(asked) == 1, "one question for several changes")
ej = q.job_for_video(vb.id, "edit")
check(ej is not None and ej.title == "Second, renamed" and "#Ace" in ej.description, "the edit carries the new title + hashtags")
check(wait(lambda: ej.state == "done", 60), "edit flow done")
check(library.get_video(vb.id).youtube_title == "Second, renamed", "the record follows")
library.rename_video(vf.id, title="Local only change")
s_ = library.get_video(ve.id)
library.rename_video(ve.id, title="never uploaded")
asked.clear()
pump(2.5)
check(len(asked) == 1, "renaming a clip that isn't on YouTube asks nothing (only the uploaded one did)")
sync.detach()

# ------------------------------------------------------------------ Delete from YouTube / Remove from afterglow
from afterglow.gui import uploaded_actions as UA  # noqa: E402
UA_ask = []
import afterglow.gui.custom_message_dialog as _cmd  # noqa: E402
check(UA.delete_from_youtube([library.get_video(va.id)]), "Delete from YouTube: confirmed first")
dj = q.job_for_video(va.id, "delete")
check(wait(lambda: dj is not None and dj.state == "done", 60), "...then automated in Studio")
check(va.id not in [x.id for x in library.list_videos()], "a deleted upload with no local file leaves the library")
check(UA.remove_from_afterglow([library.get_video(vb_.id)]), "Remove from afterglow")
vv = library.get_video(vb_.id)
check(not vv.youtube_video_id and Path(vv.path).exists() and vb_.id in [x.id for x in library.list_videos(local_only=True)],
      "...a clip with its file stays in Local, YouTube untouched")

# ------------------------------------------------------------------ crash recovery
library.mark_upload_queued(vd.id, "x", "", "", "unlisted")
library.mark_upload_state(vd.id, "uploading")
UQ.UploadQueue()
check(library.get_video(vd.id).upload_state == "failed" and "closed" in (library.get_video(vd.id).upload_error or ""),
      "an upload interrupted by a crash comes back as failed (retryable), never lost")

# ------------------------------------------------------------------ the Library cards
from afterglow.gui.library_page import LibraryPage  # noqa: E402
from afterglow.gui import youtube_player  # noqa: E402
played = []
youtube_player.play = lambda video, parent=None: played.append(video.id)
page = LibraryPage()
page.resize(1600, 1000)
page.show()
page.local_tab._do_refresh()
page.uploaded_tab._do_refresh()
pump(0.5)
up_cards = {c.video_id: c for c in page.uploaded_tab._cards}
loc_cards = {c.video_id: c for c in page.local_tab._cards}
check(vb.id in up_cards and vb.id not in loc_cards, "Uploaded tab lists the uploaded-and-trashed clip; Local doesn't")
check(vc_.id in up_cards and vc_.id in loc_cards, "a kept-file upload is in both")
card = up_cards[vb.id]
check(card.uploaded_view and str(card._thumbnail_path(library.get_video(vb.id))).endswith("SECONDvid12.jpg"),
      "Uploaded cards show YouTube's cached thumbnail")
check(not card.upload_badge.isHidden() and card.upload_badge.text() == "Unlisted", "...with the visibility as a badge")
# YouTube only had the 4:3 hqdefault (black bars baked in) -- the card must still be a rounded 16:9 image
from afterglow.gui.video_card import THUMB_SIZE  # noqa: E402
pm = card.thumb_label.pixmap()
img = pm.toImage()
check(pm.size() == THUMB_SIZE, f"the thumbnail is cropped to the card's exact size ({pm.width()}x{pm.height()})")
corners = [img.pixelColor(x, y).alpha() for x, y in ((0, 0), (pm.width() - 1, 0), (0, pm.height() - 1),
                                                         (pm.width() - 1, pm.height() - 1))]
check(all(a_ == 0 for a_ in corners), f"its corners are rounded (transparent) ({corners})")
mid = pm.width() // 2
edge_rows = [img.pixelColor(mid, y) for y in (2, pm.height() - 3)]
check(all(c.alpha() > 200 and c.red() > 120 for c in edge_rows),
      f"no letterbox bars: the top / bottom edges are picture, not black ({[c.name() for c in edge_rows]})")
check(any("/hqdefault.jpg" in h for h in F.HITS) and any("/maxresdefault.jpg" in h for h in F.HITS),
      "16:9 variants are tried first, hqdefault last")
check(loc_cards[vc_.id].upload_badge.text() == "On YouTube", "a Local card of an uploaded clip says On YouTube")
check(loc_cards[vd.id].upload_badge.text() == "Upload failed", "a failed upload says so on its card")
sizes = {c.sizeHint().height() for c in list(up_cards.values()) + list(loc_cards.values())}
check(len(sizes) == 1, f"badges float over the thumbnail: every card is still the same size ({sizes})")
from PySide6.QtTest import QTest  # noqa: E402
QTest.mouseDClick(card, Qt.LeftButton, Qt.NoModifier, card.video_box.geometry().center())
pump(0.4)
check(played == [vb.id], "double-click in Uploaded plays the YouTube embed")
previewed = []
card.preview_requested.connect(lambda *a: previewed.append(a))
QTest.mouseClick(card, Qt.LeftButton, Qt.NoModifier, card.video_box.geometry().center())
pump(0.5)
check(not previewed, "a single click in Uploaded doesn't open the local previewer")

# live badge from the queue
vg = make_clip("Live.mp4")
page.local_tab._do_refresh()
F.set_flow_timeouts(INFO, "upload", "slow=1&vid=LIVEvid1234")
lj = q.enqueue_uploads([defaults_for(vg)])[0]
seen = set()
end = time.perf_counter() + 60
verifying_since = None
while time.perf_counter() < end and not lj.finished:
    app.processEvents()
    c = next((c for c in page.local_tab._cards if c.video_id == vg.id), None)
    if c is not None and c.upload_badge.isVisible():
        seen.add(c.upload_badge.text().split(" ")[0])
    if lj.state == "verifying":
        verifying_since = verifying_since or time.perf_counter()
        if time.perf_counter() - verifying_since > 1.0:     # YouTube "processes" for a second
            F.READY.add("LIVEvid1234")
    time.sleep(0.01)
check({"Uploading", "Processing"} <= seen, f"the card's badge follows the upload live ({sorted(seen)})")

# ------------------------------------------------------------------ the red circle's click reaches the GUI
from afterglow.youtube import gui_socket  # noqa: E402
srv = gui_socket.make_server()
got = []
srv.message.connect(got.append)
import threading  # noqa: E402
threading.Thread(target=lambda: gui_socket.send({"event": "show_upload", "id": "x"}), daemon=True).start()
check(wait(lambda: bool(got), 5) and got[0] == {"event": "show_upload", "id": "x"}, "the GUI socket receives the click")
check(gui_socket.make_server() is None, "a second GUI doesn't steal the socket")
F.set_flow_timeouts(INFO, "upload", "slow=1&vid=CIRCLEvid12")
vh = make_clip("Circle.mp4")
cj = q.enqueue_uploads([defaults_for(vh)])[0]
wait(lambda: cj.state == "running" and cj.cid, 10)
check(q.show_for_cid(cj.cid) and w.is_on_screen() and "Uploading" in w.message.text(),
      "clicking the circle pops that upload's Studio window out")
w.action.emit("hide")
pump(0.3)
check(not w.is_on_screen() and cj.state == "running", "Hide tucks it away; the upload carries on")
F.READY.add("CIRCLEvid12")
wait(lambda: cj.finished, 60)
srv.close()

# ------------------------------------------------------------------ Settings > YouTube
from afterglow.gui.settings_page import SettingsPage  # noqa: E402
sp = SettingsPage()
yp = sp.youtube_page
check(yp.account_label.text() == "Signed in: Fake Channel", "the signed-in channel is shown")
yp.template_edit.setPlainText("{title} {filters}")
check(yp.preview_label.text() == "Triple kill on B #Clutch #1v3 #Ace", f"live template preview ({yp.preview_label.text()})")
yp.review_check.setChecked(True)
yp.privacy_combo.setCurrentIndex(yp.privacy_combo.findData("private"))
s = config.load()
s.youtube.account_name = "Changed Elsewhere"
config.save(s)
sp._save()
s = config.load()
check(s.youtube.description_template == "{title} {filters}" and s.youtube.stop_for_review and s.youtube.default_privacy == "private",
      "Save writes the YouTube settings")
check(s.youtube.account_name == "Changed Elsewhere", "...without overwriting the sign-in with a stale copy")
yp.circle_color_edit.setText("nope")
shown.clear()
import afterglow.gui.settings_page as SPm  # noqa: E402
SPm.show_message = lambda *a: shown.append(a[2])
sp._save()
check(shown and "upload circle" in shown[0], "an invalid circle colour is refused")
row = next(r for r in sp._rows if r.clip_config_id == cfg.id)
check(row.playlist_edit.text() == "Valorant Clips", "the clip option shows its YouTube playlist")

indicator_client.set_test_sink(None)
q.window().set_busy(False)
q.window().hide()
print()
print(f"{len(fails)} failure(s)" if fails else "ALL PASS")
sys.exit(1 if fails else 0)

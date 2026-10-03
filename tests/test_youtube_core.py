"""
YouTube upload, the parts with no web engine: the description template, link parsing, the trash,
the library's upload records (Local = file still here, Uploaded = done on YouTube, a trashed
upload is never "missing", path tombstones, restore-from-trash), the metadata listeners, config
migration from the old OAuth fields, studio_steps.json integrity, and the clip indicator's red
upload circle (model, paint, click routing to the GUI socket, the client's upload_start).

    DISPLAY=:99 QT_QPA_PLATFORM=xcb python3 tests/test_youtube_core.py      (offscreen works too)
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="yt_core_home_")
os.environ["HOME"] = HOME
os.environ["XDG_DATA_HOME"] = os.path.join(HOME, ".local", "share")
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="yt_core_run_")
os.environ["AFTERGLOW_GUI_SOCKET"] = os.path.join(os.environ["XDG_RUNTIME_DIR"], "gui.sock")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from afterglow import db, config, library, clips  # noqa: E402

fails = []


def check(c, m):
    print(("PASS " if c else "FAIL ") + m)
    if not c:
        fails.append(m)


# ------------------------------------------------------------------ config: the old OAuth fields are dropped
config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
config.CONFIG_FILE.write_text('[youtube]\nclient_secret_path = "/x.json"\ntoken_path = "/t.json"\n'
                              'default_privacy = "public"\ndefault_category_id = "20"\nlinked_account_email = "a@b"\n')
config.invalidate_cache()
s = config.load()
check(s.youtube.default_privacy == "public" and not hasattr(s.youtube, "client_secret_path"),
      "a config with the old OAuth fields still loads (unknown keys dropped, known kept)")
check(s.youtube.delete_local_after_upload and s.youtube.show_upload_circle and not s.youtube.stop_for_review,
      "upload defaults: delete local after upload ON, red circle ON, stop for review OFF")
check("{filters}" in s.youtube.description_template and "{size}" in s.youtube.description_template,
      "the default description template carries the filters and the file size")
config.CONFIG_FILE.unlink()
config.invalidate_cache()

# ------------------------------------------------------------------ template
from afterglow.youtube import template as T, parse_video_id  # noqa: E402

facts = T.ClipFacts(title="Ace", clip_type="Valorant", created_at="2026-10-03T19:17:00+00:00",
                    duration_sec=32.4, size_bytes=12_400_000, filters=["Clutch 1v3!", "ace", "...", "Ñandú"])
out = T.expand(T.DEFAULT_TEMPLATE, facts)
check(out.startswith("#Clutch1v3 #ace #Ñandú\n\nValorant -- captured "), f"filters become sanitised hashtags ({out.splitlines()[0]!r})")
check("Length 0:32 · 12.4 MB" in out, "length and size are formatted")
from datetime import datetime, timezone  # noqa: E402
local = datetime(2026, 10, 3, 19, 17, tzinfo=timezone.utc).astimezone()
check(local.strftime("%Y-%m-%d %H:%M") in out, "{date} {time} are the capture moment in local time")
bare = T.expand(T.DEFAULT_TEMPLATE, T.ClipFacts(title="x", created_at="2026-01-01T00:00:00", duration_sec=5))
check(not bare.startswith("\n") and "--" not in bare.split("\n")[0] and not bare.endswith("·"),
      f"empty placeholders leave no blank lead / dangling separators ({bare!r})")
check(T.expand("{title} {unknown} {{x}}", facts) == "Ace {unknown} {x}", "unknown placeholders stay; {{ }} escape")
check(T.expand("{title", facts) == "{title", "an unbalanced brace doesn't crash")
check(T.format_length(3725) == "1:02:05" and T.format_size(999) == "999 B", "h:mm:ss past an hour; bytes")
check(T.sanitize_for_youtube("a<b>c") == "a‹b›c", "< > become look-alikes (YouTube rejects them)")
check(len(T.sanitize_for_youtube("é" * 4000, max_bytes=5000).encode()) <= 5000, "description capped at 5000 bytes")
check(any("100" in p for p in T.problems("x" * 101, "")) and T.problems("ok", "fine") == [], "title length checked")
check(parse_video_id("https://youtu.be/dQw4w9WgXcQ?si=x") == "dQw4w9WgXcQ"
      and parse_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=3") == "dQw4w9WgXcQ"
      and parse_video_id("https://studio.youtube.com/video/dQw4w9WgXcQ/edit") == "dQw4w9WgXcQ"
      and parse_video_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
      and parse_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ" and parse_video_id("nope") is None,
      "video ids parse from every link shape")

# ------------------------------------------------------------------ trash
from afterglow.youtube import trash  # noqa: E402

f1 = Path(HOME) / "a clip.mp4"
f1.write_bytes(b"x" * 10)
trash.trash(f1, use_gio=False)
tdir = trash.home_trash()
check(not f1.exists() and (tdir / "files" / "a clip.mp4").exists(), "spec trash: the file moved into Trash/files")
info = (tdir / "info" / "a clip.mp4.trashinfo").read_text()
check("Path=" in info and "a%20clip.mp4" in info and "DeletionDate=" in info, "...with a .trashinfo record (restorable)")
f1.write_bytes(b"y")
trash.trash(f1, use_gio=False)
check((tdir / "files" / "a clip.2.mp4").exists(), "a second file of the same name gets a unique trash name")
d1 = Path(HOME) / "clip.mp4.input"
d1.mkdir()
(d1 / "m.json").write_text("{}")
trash.trash(d1, use_gio=False)
check(not d1.exists() and (tdir / "files" / "clip.mp4.input" / "m.json").exists(), "directories (overlay sidecars) too")
trash.trash(Path(HOME) / "missing.mp4")
check(True, "trashing a missing file is a no-op")

# ------------------------------------------------------------------ library records
db.init_db()
CLIPS = config.load().clips_path()
CLIPS.mkdir(parents=True, exist_ok=True)


def make_clip(name: str) -> Path:
    p = CLIPS / name
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x180:rate=30",
                    "-c:v", "libx264", "-preset", "ultrafast", str(p)], check=True)
    return p


cfg = clips.create_clip_config("Valorant", 30, youtube_playlist="Valorant Clips")
check(clips.get_clip_config(cfg.id).youtube_playlist == "Valorant Clips", "a clip type keeps its YouTube playlist")
check(clips.update_clip_config(cfg.id, youtube_playlist="Other").youtube_playlist == "Other", "...and it can change")
clips.update_clip_config(cfg.id, youtube_playlist="Valorant Clips")
pa, pb, pc = make_clip("A.mp4"), make_clip("B.mp4"), make_clip("C.mp4")
va = library.add_video(pa, "Clip A", clip_config_id=cfg.id)
vb = library.add_video(pb, "Clip B")
vc = library.add_video(pc, "Clip C")
library.add_tag_to_video(va.id, "Clutch")

ids = lambda **kw: sorted(v.id for v in library.list_videos(**kw))  # noqa: E731
check(ids(local_only=True) == sorted([va.id, vb.id, vc.id]) and ids(uploaded_only=True) == [], "fresh clips: all Local")
library.mark_upload_queued(va.id, "Clip A", "desc", "Valorant Clips", "unlisted")
v = library.get_video(va.id)
check(v.upload_state == "queued" and v.file_size_bytes == pa.stat().st_size and v.youtube_playlist == "Valorant Clips",
      "queued: state, playlist and the file size are remembered")
library.mark_upload_state(va.id, "uploading", pending_id="AAAAAAAAAAA")
check(ids(uploaded_only=True) == [] and va.id in ids(local_only=True), "uploading: still only Local (not done yet)")
library.mark_upload_done(va.id, "AAAAAAAAAAA", "/thumb.jpg")
v = library.get_video(va.id)
check(v.is_uploaded and v.youtube_url == "https://youtu.be/AAAAAAAAAAA" and v.uploaded_at, "done: on YouTube")
check(va.id in ids(uploaded_only=True) and va.id in ids(local_only=True), "done with the file kept: in BOTH tabs")

library.trash_local_file(va.id)
v = library.get_video(va.id)
check(v.local_deleted and not pa.exists() and (trash.home_trash() / "files" / "A.mp4").exists(),
      "trash_local_file: the file is in the system trash, the record says local-deleted")
check(va.id in ids(uploaded_only=True) and va.id not in ids(local_only=True), "...Uploaded only now")
check(library.prune_missing_videos() == [] and library.get_video(va.id), "a trashed upload is never pruned as missing")
check("youtube_video_id" in json.loads(library.MANIFEST_PATH.read_text())["videos"][0], "the manifest records YouTube ids")

# a new file at the same path (re-capture with the same title) must not collide with the record
pa2 = make_clip("A.mp4")
new = library.add_video(pa2, "Clip A again")
check(new.path == str(pa2) and library.get_video(va.id).path.endswith(f"#uploaded-{va.id}"),
      "a new file at an uploaded clip's old path gets its own row (the old one is tombstoned)")
library.delete_video(new.id)

# restore from the trash: the clip is local again
library.mark_upload_done(vb.id, "BBBBBBBBBBB")
library.trash_local_file(vb.id)
import shutil  # noqa: E402
shutil.move(str(trash.home_trash() / "files" / "B.mp4"), str(pb))
library.scan_and_ingest_new_videos()
check(not library.get_video(vb.id).local_deleted and vb.id in ids(local_only=True),
      "a trashed file restored to its old place makes the clip local again (no duplicate row)")
check(sum(1 for x in library.list_videos() if x.path == str(pb)) == 1, "...exactly one row for it")

# Delete on a clip that is also on YouTube keeps the YouTube record
library.delete_local_copy(vb.id)
check(not pb.exists() and library.get_video(vb.id).is_uploaded and vb.id not in ids(local_only=True),
      "Library Delete on an uploaded clip: file erased, Uploaded record kept")

# forget_youtube: Remove from afterglow
library.mark_upload_done(vc.id, "CCCCCCCCCCC")
kept = library.forget_youtube(vc.id)
check(kept is not None and not kept.youtube_video_id and vc.id in ids(local_only=True),
      "Remove from afterglow on a clip with its file: back to a plain Local clip")
gone = library.forget_youtube(vb.id)
check(gone is None and vb.id not in [x.id for x in library.list_videos()], "...and one without a file: the row goes")

# failed / cancelled
library.mark_upload_queued(vc.id, "C", "", "", "unlisted")
library.mark_upload_state(vc.id, "failed", error="boom")
check(library.get_video(vc.id).upload_error == "boom" and vc.id not in ids(uploaded_only=True), "failed: error kept")
check([x.id for x in library.list_upload_states(("failed",))] == [vc.id], "list_upload_states finds it")
library.cancel_upload_record(vc.id)
check(library.get_video(vc.id).upload_state is None, "cancelled: a plain clip again")

# register a manual (browser) upload
r = library.register_youtube_link(vc.id, "DDDDDDDDDDD", "public", title="T", description="D")
check(r.is_uploaded and r.youtube_privacy == "public" and r.youtube_title == "T", "manual link registration")
library.forget_youtube(vc.id)

# metadata listeners
seen = []
library.metadata_listeners.append(lambda vid, kind: seen.append((vid, kind)))
library.rename_video(vc.id, title="Clip C renamed")
library.add_tag_to_video(vc.id, "Ace")
library.add_tag_to_video(vc.id, "Ace")          # no change: no event
library.remove_tag_from_video(vc.id, "Ace")
library.remove_tag_from_video(vc.id, "Ace")     # no change: no event
check(seen == [(vc.id, "title"), (vc.id, "tags"), (vc.id, "tags")], f"listeners hear real changes only ({seen})")
library.metadata_listeners.append(lambda vid, kind: 1 / 0)
library.rename_video(vc.id, title="Clip C again")
check(library.get_video(vc.id).title == "Clip C again", "a failing listener never breaks the write")
library.metadata_listeners.clear()

# description from a real video
v = library.get_video(va.id)
d = T.describe(v, T.DEFAULT_TEMPLATE)
check(d.startswith("#Clutch\n") and "Valorant -- captured" in d and ("MB" in d or "KB" in d),
      f"describe() uses the clip type, the filters and the remembered size after the file is gone ({d!r})")

# ------------------------------------------------------------------ studio_steps.json
from afterglow.youtube import steps  # noqa: E402

data = steps.load()
missing = [(f, st["id"], st["sel"]) for f, flow in data["flows"].items() for st in flow
           if st.get("sel") and st["sel"] not in data["selectors"]]
check(not missing, f"every step's selector is defined ({missing})")
kinds = {st["kind"] for flow in data["flows"].values() for st in flow}
from afterglow.youtube.studio import FlowRunner  # noqa: E402
check(all(hasattr(FlowRunner, "_step_" + k) for k in kinds), f"every step kind is implemented ({sorted(kinds)})")
check(steps.selectors(data, "privacy_radio", PRIVACY="UNLISTED")[0].endswith("[name=UNLISTED]"), "privacy selector formats")
check(steps.percent(data, "Uploading 45% ... 2 minutes left") == 45, "progress percent parses")
up, ing = steps.patterns(data, "uploaded"), steps.patterns(data, "uploading")
check(steps.matches("Upload complete ... Processing will begin shortly", up)
      and not steps.matches("Upload complete ... Processing will begin shortly", ing)
      and steps.matches("Uploading 12% ...", ing), "status texts classify")
check(steps.matches("Daily upload limit reached", steps.patterns(data, "error")), "errors classify")

# ------------------------------------------------------------------ the red upload circle
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])
from afterglow.indicator import EVENTS, model as M  # noqa: E402

check(all(e in EVENTS for e in ("upload_start", "upload_progress", "upload_done", "upload_fail")), "protocol events")
m = M.Model()
st = {"kind": "upload", "upload_color": "#ff0033", "size": 156}
m.event("u1", "upload_start", 0.0, st, "S")
ind = m._inds["u1"]
check(ind.style.kind == "upload" and ind.state == "circle_in", "upload_start: straight to the circle (no clapper)")
m.tick(0.5)
check(ind.state == "circle", "...then a steady circle")
m.tick(M.WATCHDOG - 10)
m.event("u1", "upload_progress", M.WATCHDOG - 10)
m.tick(M.WATCHDOG + 100)
check(ind.state == "circle", "heartbeats keep a long upload's circle alive past the watchdog")
m.event("u1", "upload_done", M.WATCHDOG + 101)
check(ind.state == "circle_out" and ind.pulse_t0 >= 0, "upload_done: fades out with the pulse")
m.tick(M.WATCHDOG + 110)
check(not m.has("u1"), "...and is gone")
m.event("u2", "upload_start", 0.0, st, "S")
m.tick(1.0)
m.event("u2", "upload_fail", 1.0)
check(m._inds["u2"].state == "circle_fail", "upload_fail: the existing fail shake")
m.tick(3.0)
check(not m.has("u2"), "...then gone")
m.event("u3", "upload_start", 0.0, st, "S")
m.tick(1.0)
m.tick(1.0 + M.WATCHDOG + 1)
m.tick(1.0 + M.WATCHDOG + 5)
check(not m.has("u3"), "no heartbeat (the GUI died): the watchdog winds it down")

# paint: clickable while running, red, play glyph; not the throttle label
from afterglow.indicator.paint import StackPainter  # noqa: E402
from afterglow.indicator import layout  # noqa: E402
from PySide6.QtGui import QImage, QPainter, QColor  # noqa: E402
m = M.Model()
m.event("u4", "upload_start", 0.0, st, "S")
m.tick(1.0)
key = ("S", "bottom_right")
W, H = layout.surface_size("bottom_right", 156, 32, 32, (1920, 1080))
sp = StackPainter("bottom_right", 156, 32, 32, (W, H))
tg = sp.click_targets(m, key, 1.0)
check(len(tg) == 1 and tg[0][1] == "u4", "the running upload circle is clickable (even with throttling off)")
img = QImage(int(W), int(H), QImage.Format_ARGB32_Premultiplied)
img.fill(0)
p = QPainter(img)
sp.paint_stack(p, m, key, 1.0)
p.end()
r = tg[0][0]
reds = 0
for x in range(int(r.left()), int(r.right()), 2):
    for y in range(int(r.top()), int(r.bottom()), 2):
        c = img.pixelColor(x, y)
        if c.alpha() > 40 and c.red() > 120 and c.green() < 80 and c.blue() < 90:
            reds += 1
centre = img.pixelColor(int(r.center().x()), int(r.center().y()))
check(reds > 20, f"the circle is drawn in YouTube red ({reds} red samples)")
check(centre.alpha() > 40 and centre.red() > 150 and centre.green() > 150, "a white play glyph sits in its centre")
sp.bypass = True
m2 = M.Model()
m2.event("u5", "upload_start", 0.0, st, "S")
m2.tick(1.0)
img2 = QImage(int(W), int(H), QImage.Format_ARGB32_Premultiplied)
img2.fill(0)
p = QPainter(img2)
sp.paint_stack(p, m2, key, 1.0)
p.end()
top = sum(1 for x in range(0, int(W), 3) for y in range(0, max(1, int(r.top()) - 2), 3) if img2.pixelColor(x, y).alpha() > 0)
check(top == 0, "no THROTTLING = OFF label over an upload circle")

# helper: a click on an upload circle reaches the GUI socket; a click on a processing element still toggles the bypass
received = []
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(os.environ["AFTERGLOW_GUI_SOCKET"])
srv.listen(4)


def serve():
    while True:
        try:
            c, _ = srv.accept()
        except OSError:
            return
        received.append(c.recv(4096).decode())
        c.close()


threading.Thread(target=serve, daemon=True).start()
from afterglow.indicator.helper import IndicatorHelper  # noqa: E402
NOW = [0.0]
h = IndicatorHelper(clock=lambda: NOW[0], force_layer_shell=False)
h.handle({"id": "up-1", "event": "upload_start", "style": st})
NOW[0] = 1.0
h.step()
h.on_element_click("up-1")
time.sleep(0.3)
check(received and json.loads(received[0]) == {"event": "show_upload", "id": "up-1"},
      f"clicking the upload circle asks the GUI to show it ({received})")
check(not h.bypass, "...and does not touch the throttle bypass")
h.on_element_click(None)
check(h.bypass, "a click with no upload id still toggles the bypass (processing element)")
h.set_bypass(False)
surfs = list(h.surfaces.values())
if surfs:
    surf = surfs[0]
    surf.update_clicks(NOW[0])
    check(surf._click_ids == ["up-1"] and surf._click_rects, "the surface knows which id each click rect belongs to")
    hit, cid = surf._target_at(surf._click_rects[0].center())
    check(hit and cid == "up-1", "a click inside the rect resolves to that upload")
for s_ in list(h.surfaces.values()):
    s_.hide()

# the client: begin_upload sends upload_start with kind=upload and the configured colour
from afterglow import indicator_client  # noqa: E402
sent = []
indicator_client.set_test_sink(sent.append)
s = config.load()
s.youtube.upload_circle_color = "#123456"
config.save(s)
cid = indicator_client.begin_upload()
indicator_client.emit(cid, "upload_progress")
indicator_client.emit(cid, "upload_done")
indicator_client.emit(cid, "upload_start")      # never re-sent through emit
indicator_client.flush(5)
check(cid and cid.startswith("upload-") and sent[0]["event"] == "upload_start"
      and sent[0]["style"]["kind"] == "upload" and sent[0]["style"]["upload_color"] == "#123456",
      "begin_upload: upload_start with the upload style")
check([x["event"] for x in sent] == ["upload_start", "upload_progress", "upload_done"], "then progress / done")
s.youtube.show_upload_circle = False
config.save(s)
check(indicator_client.begin_upload() is None, "the red circle can be switched off (Settings > YouTube)")
indicator_client.set_test_sink(None)
srv.close()

print()
print(f"{len(fails)} failure(s)" if fails else "ALL PASS")
sys.exit(1 if fails else 0)

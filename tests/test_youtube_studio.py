"""
The YouTube Studio driver against a local fake Studio (tests/fake_studio/, same selectors as
studio_steps.json), in a REAL QtWebEngine page: the hidden window (shown with
WA_DontShowOnScreen -- Chromium must see it as visible), the real click that opens the file picker
answered by chooseFiles, multi-line description entry, playlist / made-for-kids / visibility, the
status text walking to "Upload complete", review mode, every failure path (missing playlist,
picker never appears, YouTube error status, signed out, missing file, cancel), the edit and delete
flows, the account flow, and the sign-in user-agent fix.

Needs Xvfb + QtWebEngine (and, when run as root, QTWEBENGINE_DISABLE_SANDBOX=1):
    DISPLAY=:99 QT_QPA_PLATFORM=xcb QTWEBENGINE_DISABLE_SANDBOX=1 python3 tests/test_youtube_studio.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="yt_studio_home_")
os.environ["HOME"] = HOME
os.environ["XDG_DATA_HOME"] = os.path.join(HOME, ".local", "share")
if os.geteuid() == 0:
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import yt_fixtures as F  # noqa: E402

INFO = F.start()

from PySide6.QtCore import QCoreApplication, Qt, QUrl  # noqa: E402
QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication([])

# "localhost" plays accounts.google.com for the user-agent checks (set before the profile exists)
from afterglow.youtube import profile as P  # noqa: E402
P.SIGNIN_HOSTS = P.SIGNIN_HOSTS + ("localhost",)
from afterglow.gui.studio_window import StudioWindow  # noqa: E402
from afterglow.youtube import studio as S  # noqa: E402

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


def js(page, code, timeout=10):
    """Run JS in the page's main world; the result round-trips through JSON."""
    import json as _j
    box = {}
    page.runJavaScript(f"JSON.stringify(({code}))", 0, lambda r: box.setdefault("r", r))
    end = time.perf_counter() + timeout
    while "r" not in box and time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)
    r = box.get("r")
    try:
        return _j.loads(r) if isinstance(r, str) else r
    except ValueError:
        return r


def run(flow, values, timeout=60, on_needs_user=None, cancel_after=None):
    ev = {"steps": [], "status": [], "pct": [], "links": [], "done": None, "failed": None, "needs": []}
    r = S.FlowRunner(win.page, flow, values, view=win.view, pace_ms=30)
    r.step_started.connect(ev["steps"].append)
    r.status_text.connect(ev["status"].append)
    r.percent.connect(ev["pct"].append)
    r.link_found.connect(ev["links"].append)
    r.needs_user.connect(lambda w: (ev["needs"].append(w), on_needs_user and on_needs_user()))
    r.finished.connect(lambda res: ev.__setitem__("done", res))
    r.failed.connect(lambda a, b, c: ev.__setitem__("failed", (a, b, c)))
    r.start()
    t0 = time.perf_counter()
    while ev["done"] is None and ev["failed"] is None and time.perf_counter() - t0 < timeout:
        app.processEvents()
        time.sleep(0.005)
        if cancel_after and ev["steps"] and ev["steps"][-1] == cancel_after:
            r.cancel()
            cancel_after = None
    ev["runner"] = r
    return ev


clip = Path(HOME) / "My Clip.mp4"
clip.write_bytes(b"\0" * 4096)
DESC = "#Clutch #ace\n\nValorant -- captured 2026-10-03 14:17\nLength 0:32 · 12.4 MB"

# ------------------------------------------------------------------ the hidden window
win = StudioWindow()
win.set_busy(True)
win.run_hidden()
pump(0.5)
check(win.isVisible() and not win.is_on_screen(), "run_hidden: visible to Qt, never mapped on screen")
win.page.load(QUrl(INFO["base"] + "/home.html"))
pump(2.0)
check(js(win.page, "document.visibilityState") == "visible", "...and Chromium sees the page as visible (no throttling)")

# ------------------------------------------------------------------ a full upload
F.set_flow_timeouts(INFO, "upload", "slow=1")
ev = run("upload", {"file": str(clip), "title": "Ace on B", "description": DESC, "playlist": "Valorant Clips",
                    "privacy": "unlisted"})
check(ev["done"] is not None and ev["failed"] is None, f"the upload flow completes ({ev['failed']})")
check(ev["done"] and ev["done"].get("video_id") == "FAKEvid1234", "the new video's id is read back")
check(ev["links"] and ev["steps"].index("link") < ev["steps"].index("uploaded"),
      "...as soon as Studio shows it (before the upload finished)")
fake = js(win.page, "window.__fake")
check(fake and fake["file"] == "My Clip.mp4", "the picker was answered with the clip's file (no OS dialog)")
check(win.page.picker_calls == 1, "exactly one file picker opened")
check(fake and fake["title"] == "Ace on B", "title replaced (not appended to Studio's filename default)")
check(fake and fake["description"] == DESC, "multi-line description entered exactly")
check(fake and fake["playlists"] == ["Valorant Clips"], "playlist ticked by name")
check(fake and fake["kids"] == "VIDEO_MADE_FOR_KIDS_NOT_MFK", "“not made for kids” answered")
check(fake and fake["privacy"] == "UNLISTED", "visibility: unlisted")
check(fake and fake["saved"] and fake["closed"], "Save pressed, the share dialog closed")
check(len(set(ev["pct"])) >= 3 and max(ev["pct"]) == 100, f"progress reported along the way ({sorted(set(ev['pct']))[:6]}...)")
check(any("Uploading" in s for s in ev["status"]), "Studio's own status text is passed through")
check(ev["steps"].index("uploaded") < ev["steps"].index("save"), "Save only once the upload is complete")

# ------------------------------------------------------------------ public visibility, no playlist
F.set_flow_timeouts(INFO, "upload", "vid=PUBvid12345")
ev = run("upload", {"file": str(clip), "title": "P", "description": "", "playlist": "", "privacy": "public"})
fake = js(win.page, "window.__fake")
check(ev["done"] and fake["privacy"] == "PUBLIC" and fake["playlists"] == [] and ev["done"]["video_id"] == "PUBvid12345",
      "public, no playlist (the playlist step is skipped)")

# ------------------------------------------------------------------ stop for review
F.set_flow_timeouts(INFO, "upload", "")
ev = run("upload", {"file": str(clip), "title": "R", "description": "d", "playlist": "", "privacy": "private",
                    "review": True},
         on_needs_user=lambda: js(win.page, "document.querySelector('#done-button').click()"))
check(ev["needs"] == ["review"], "review mode stops before Save and asks the user")
check(ev["done"] and ev["done"].get("saved_by_user"), "the user's own Save finishes the run")
check(js(win.page, "window.__fake.privacy") == "PRIVATE", "...with everything filled in first")

# ------------------------------------------------------------------ failures
ev = run("upload", {"file": str(clip), "title": "X", "description": "", "playlist": "Nope", "privacy": "unlisted"})
check(ev["failed"] and ev["failed"][0] == "playlist" and "Highlights" in ev["failed"][1],
      f"a missing playlist stops at that step and names the ones found ({ev['failed']})")

F.set_flow_timeouts(INFO, "upload", "fail=picker", file_picker=1500)
ev = run("upload", {"file": str(clip), "title": "X", "description": "", "playlist": "", "privacy": "unlisted"})
check(ev["failed"] and ev["failed"][0] == "file_picker" and ev["failed"][2] == "error",
      f"a control that never appears: the step times out with the selector named ({ev['failed']})")

F.set_flow_timeouts(INFO, "upload", "error=1&slow=1", file_picker=45000)
S.ERROR_GRACE_S = 1.0
ev = run("upload", {"file": str(clip), "title": "X", "description": "", "playlist": "", "privacy": "unlisted"})
check(ev["failed"] and ev["failed"][0] == "uploaded" and "network error" in ev["failed"][1],
      f"YouTube's own error status stops the run ({ev['failed']})")
S.ERROR_GRACE_S = 30.0

F.set_flow_timeouts(INFO, "upload", "")
ev = run("upload", {"file": str(Path(HOME) / "gone.mp4"), "title": "X", "description": "", "playlist": "",
                    "privacy": "unlisted"})
check(ev["failed"] and ev["failed"][0] == "choose_file" and "missing" in ev["failed"][1], "a missing file fails safe")

ev = run("upload", {"file": str(clip), "title": "X", "description": "", "playlist": "", "privacy": "unlisted"},
         cancel_after="title")
check(ev["failed"] and ev["failed"][2] == "cancelled" and "privacy" not in ev["steps"], "cancel stops the run")

# signed out: Studio bounces to the sign-in host
import json  # noqa: E402
data = json.loads(Path(INFO["steps_path"]).read_text())
data["upload_url"] = INFO["signin_base"] + "/signin.html"
Path(INFO["steps_path"]).write_text(json.dumps(data))
ev = run("upload", {"file": str(clip), "title": "X", "description": "", "playlist": "", "privacy": "unlisted"})
check(ev["failed"] and ev["failed"][2] == "signed_out" and ev["failed"][0] == "open", f"signed out is its own kind ({ev['failed']})")
F.set_flow_timeouts(INFO, "upload", "")

# ------------------------------------------------------------------ edit / delete / account
ev = run("edit", {"video_id": "FAKEvid1234", "title": "New title", "description": "Line 1\nLine 2"})
fake = js(win.page, "window.__fake")
check(ev["done"] is not None and fake["vid"] == "FAKEvid1234", f"the edit flow opens that video's page ({ev['failed']})")
check(fake["saved"] == {"title": "New title", "description": "Line 1\nLine 2"}, f"title / description saved ({fake['saved']})")

ev = run("delete", {"video_id": "FAKEvid1234"})
check(ev["done"] is not None and js(win.page, "window.__fake.deleted") is True,
      f"the delete flow: menu -> Delete forever -> confirm checkbox -> confirm ({ev['failed']})")

ev = run("account", {})
check(ev["done"] and ev["done"].get("account") == "Fake Channel Name", "the account flow reads the channel name")

# ------------------------------------------------------------------ pop out / tuck away
win.pop_out()
pump(0.5)
check(win.is_on_screen(), "pop_out: on screen")
win.tuck_away()
pump(0.3)
check(win.isVisible() and not win.is_on_screen(), "tuck_away while busy: hidden but still alive")
from PySide6.QtGui import QCloseEvent  # noqa: E402
win.pop_out()
pump(0.3)
win.close()
pump(0.3)
check(win.isVisible() and not win.is_on_screen(), "closing it while busy only tucks it away (the upload lives in it)")
check(js(win.page, "1+1") == 2, "...and the page is still alive")
win.set_busy(False)
win.tuck_away()
pump(0.3)
check(not win.isVisible(), "not busy: hides for real")

# ------------------------------------------------------------------ the sign-in user agent
from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: E402


def load(pg, url):
    box = {}
    pg.loadFinished.connect(lambda ok: box.setdefault("ok", ok))
    pg.load(QUrl(url))
    end = time.perf_counter() + 10
    while "ok" not in box and time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.005)


pg = QWebEnginePage(P.profile())
load(pg, INFO["base"] + "/signin.html")
ua = js(pg, "navigator.userAgent")
check(ua and "Chrome" in ua and "Firefox" not in ua, "other hosts (Studio) see the engine's normal user agent")
load(pg, INFO["signin_base"] + "/signin.html")
ua = js(pg, "navigator.userAgent")
data_ = js(pg, "JSON.stringify([navigator.vendor, typeof navigator.userAgentData, typeof window.chrome])")
check(ua == P.FIREFOX_UA, f"the sign-in host sees a Firefox navigator.userAgent ({ua})")
check(data_ == '["","undefined","undefined"]', f"...and no Chromium tells (vendor / userAgentData / window.chrome) ({data_})")
load(pg, INFO["signin_base"] + "/ua")
import json as _json  # noqa: E402
hdr = _json.loads(js(pg, "document.body.innerText") or "{}")
check(hdr.get("ua") == P.FIREFOX_UA, f"...and a Firefox User-Agent header ({hdr.get('ua')})")
check(not hdr.get("ch"), f"...with the Chromium client hints blanked ({hdr.get('ch')!r})")
load(pg, INFO["base"] + "/ua")
hdr2 = _json.loads(js(pg, "document.body.innerText") or "{}")
check("Firefox" not in hdr2.get("ua", "") and "Chrome" in hdr2.get("ua", ""), "Studio's requests keep the normal header")
check(P.is_signin_url("https://accounts.google.com/v3/signin") and not P.is_signin_url("https://studio.youtube.com/"),
      "sign-in hosts recognised")

win.hide()
print()
print(f"{len(fails)} failure(s)" if fails else "ALL PASS")
sys.exit(1 if fails else 0)

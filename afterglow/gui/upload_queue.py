"""
The YouTube job queue (GUI process): uploads, metadata edits and deletions, one at a time, in the
one hidden Studio window (gui/studio_window.py) driven by youtube/studio.FlowRunner.

An upload's life:
  queued -> running (Studio automation, hidden; the clip indicator shows the red circle)
         -> verifying (Studio said uploaded + saved; off the GUI thread, YouTube's public oEmbed /
            thumbnail are polled until the video really plays -- the next job already runs)
         -> done: recorded (Uploaded tab, thumbnail cached), the local file moved to the TRASH if
            "Delete the local file after uploading" is on, the upload-done sound, the circle pulses out
  or     -> waiting_user: a step failed (signed out, Studio changed, network...).  The circle
            shakes, the error sound plays, the Studio window pops out where it stopped; the banner
            offers Retry / "I finished it here" / Cancel.  The queue waits for that answer (the
            page IS the upload).  The local file is never touched on a failure.

Edits and deletions use the same window and the same failure handling.  Nothing here blocks the
GUI thread.
"""
from __future__ import annotations

import itertools
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from .. import config as config_module, library, indicator_client
from ..youtube import parse_video_id, steps as steps_module
from ..indicator import UPLOAD_HEARTBEAT

VERIFY_TIMEOUT = 20 * 60          # s: how long YouTube gets to make the video watchable
VERIFY_INTERVAL = 15              # s between oEmbed polls
THUMB_TRIES = 8


_KEYS = itertools.count(1)       # job keys: small ints (they cross a Qt signal)


@dataclass
class Job:
    kind: str                      # "upload" | "edit" | "delete"
    video_id: int
    title: str = ""
    description: str = ""
    playlist: str = ""
    privacy: str = "unlisted"
    file: str = ""
    yt_id: str = ""
    cid: "str | None" = None       # the indicator's id for the red circle
    state: str = "queued"          # queued running waiting_user verifying done failed cancelled
    percent: int = -1
    status: str = ""
    error: str = ""
    step: str = ""
    processed_seen: bool = False   # Studio's status said processed (private uploads rely on it)
    key: int = field(default_factory=lambda: next(_KEYS))

    @property
    def finished(self) -> bool:
        return self.state in ("done", "failed", "cancelled")

    def label(self) -> str:
        return self.title or f"clip {self.video_id}"


class _Bridge(QObject):
    verified = Signal(object, bool, str, str)   # job key, watchable, thumbnail path ("" = none), note


class UploadQueue(QObject):
    """Runs in the YouTube host process (youtube/host.py) -- never in the main GUI, whose app-wide
    Python event filters crash QtWebEngine (see host.py).  The GUI talks to it through
    gui/youtube_client.RemoteUploadQueue, which has the same methods and signals."""
    changed = Signal()                          # any job's state / progress changed
    job_finished = Signal(object)               # Job (done / failed / cancelled)
    idle = Signal()                             # nothing queued, running, waiting or verifying
    signed_in = Signal(str)                     # the sign-in window finished (channel name)

    def __init__(self, parent=None, window_factory=None):
        super().__init__(parent)
        self.jobs: "list[Job]" = []
        self.current: "Job | None" = None
        self._runner = None
        self._window = None
        self._window_factory = window_factory
        self._bridge = _Bridge()
        self._bridge.verified.connect(self._on_verified)
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(int(UPLOAD_HEARTBEAT * 1000))
        self._heartbeat.timeout.connect(self._beat)
        self._recover_interrupted()

    # ------------------------------------------------------------------ public API

    def enqueue_uploads(self, requests: "list[dict]") -> "list[Job]":
        """requests: [{video_id, title, description, playlist, privacy}].  Skips clips already
        queued / running."""
        added = []
        active = {(j.kind, j.video_id) for j in self.jobs if not j.finished}
        for r in requests:
            vid = int(r["video_id"])
            if ("upload", vid) in active:
                continue
            v = library.get_video(vid)
            job = Job("upload", vid, title=r.get("title", v.title), description=r.get("description", ""),
                      playlist=r.get("playlist", ""), privacy=r.get("privacy", "unlisted"), file=v.path)
            library.mark_upload_queued(vid, job.title, job.description, job.playlist, job.privacy)
            self.jobs.append(job)
            added.append(job)
        self.changed.emit()
        QTimer.singleShot(0, self._kick)
        return added

    def enqueue_edit(self, video_id: int, title: str, description: str) -> Job:
        v = library.get_video(video_id)
        for j in self.jobs:                     # a newer edit replaces a still-queued one
            if j.kind == "edit" and j.video_id == video_id and j.state == "queued":
                j.title, j.description = title, description
                return j
        job = Job("edit", video_id, title=title, description=description, yt_id=v.youtube_video_id or "")
        self.jobs.append(job)
        self.changed.emit()
        QTimer.singleShot(0, self._kick)
        return job

    def enqueue_delete(self, video_id: int) -> Job:
        v = library.get_video(video_id)
        job = Job("delete", video_id, title=v.youtube_title or v.title, yt_id=v.youtube_video_id or "")
        self.jobs.append(job)
        self.changed.emit()
        QTimer.singleShot(0, self._kick)
        return job

    def register_manual(self, r: dict) -> Job:
        """The browser fallback: the user uploaded by hand and pasted the link.  Verified and recorded
        like any upload (the red circle, the sounds and the trash setting included)."""
        vid = int(r["video_id"])
        v = library.get_video(vid)
        job = Job("upload", vid, title=r.get("title", v.title), description=r.get("description", ""),
                  playlist=r.get("playlist", ""), privacy=r.get("privacy", "unlisted"), file=v.path,
                  yt_id=r["yt_id"])
        library.mark_upload_queued(vid, job.title, job.description, job.playlist, job.privacy)
        self.jobs.append(job)
        job.cid = indicator_client.begin_upload(config_module.load_readonly())
        if job.cid:
            self._heartbeat.start()
        self._begin_verify(job, by_hand=True)
        return job

    def busy(self) -> bool:
        return any(not j.finished for j in self.jobs)

    def active_jobs(self) -> "list[Job]":
        return [j for j in self.jobs if not j.finished]

    def job_for_video(self, video_id: int, kind: str = "upload") -> "Job | None":
        for j in reversed(self.jobs):
            if j.video_id == video_id and j.kind == kind:
                return j
        return None

    def cancel(self, video_id: int) -> None:
        """Drop a queued upload, or stop the running / waiting one (the Studio draft, if any, is
        left in Studio; the local file is untouched)."""
        for j in self.jobs:
            if j.video_id == video_id and not j.finished and j.state != "verifying":
                if j is self.current:
                    self._resolve_current("cancel")
                else:
                    if j.kind == "upload":
                        library.cancel_upload_record(j.video_id)
                    self._finish(j, "cancelled")
        self.changed.emit()

    # ------------------------------------------------------------------ other YouTube windows

    def play(self, video_id: int) -> None:
        from . import youtube_player
        youtube_player.play(library.get_video(video_id))

    def edit_in_studio(self, video_id: int) -> None:
        from . import uploaded_actions
        uploaded_actions.open_studio_editor(library.get_video(video_id))

    def sign_in(self) -> None:
        from .youtube_signin import SignInWindow
        w = SignInWindow()
        w.setAttribute(Qt.WA_DeleteOnClose, True)
        w.signed_in.connect(self.signed_in.emit)
        self._signin_window = w
        w.start()

    def sign_out(self) -> bool:
        if self.busy():
            return False
        from ..youtube import profile
        profile.sign_out()
        s = config_module.load()
        s.youtube.account_name = ""
        config_module.save(s)
        return True

    def window(self):
        if self._window is None:
            if self._window_factory is not None:
                self._window = self._window_factory()
            else:
                from .studio_window import StudioWindow
                self._window = StudioWindow()
            self._window.action.connect(self._on_banner_action)
        return self._window

    def show_for_cid(self, cid: str) -> bool:
        """The red indicator circle was clicked: show where that upload is."""
        job = next((j for j in self.jobs if j.cid == cid), None)
        if job is None:
            return False
        self.show_job(job)
        return True

    def show_job(self, job: Job) -> None:
        w = self.window()
        if job is self.current and job.state == "running":
            w.set_banner(f"Uploading “{job.label()}” -- {job.status or 'working'}. "
                         "Hiding this window doesn't stop it.", ("hide",))
        elif job is self.current and job.state == "waiting_user":
            pass                                  # the failure banner is already up
        elif job.state == "verifying":
            w.set_banner(f"“{job.label()}” is uploaded; waiting for YouTube to finish processing it.",
                         ("hide",))
        else:
            w.set_banner(f"“{job.label()}”: {job.state}", ("hide",))
        w.pop_out()

    def show_current(self) -> None:
        if self.current is not None:
            self.show_job(self.current)
        else:
            self.window().pop_out()

    # ------------------------------------------------------------------ the loop

    def _kick(self) -> None:
        if self.current is not None:
            return
        nxt = next((j for j in self.jobs if j.state == "queued"), None)
        if nxt is None:
            self._maybe_idle()
            return
        self._start(nxt)

    def _start(self, job: Job) -> None:
        from ..youtube.studio import FlowRunner
        settings = config_module.load_readonly()
        self.current = job
        job.state, job.error, job.step, job.percent, job.status = "running", "", "", -1, ""
        w = self.window()
        w.set_busy(True)
        w.set_banner(self._running_text(job), ("hide",))
        if not w.is_on_screen():
            w.run_hidden()
        values = {"title": job.title, "description": job.description, "playlist": job.playlist,
                  "privacy": job.privacy, "file": job.file, "video_id": job.yt_id,
                  "review": bool(settings.youtube.stop_for_review)}
        if job.kind == "upload":
            library.mark_upload_state(job.video_id, "uploading")
            if not job.cid:
                job.cid = indicator_client.begin_upload(settings)
            self._heartbeat.start()
        flow = {"upload": "upload", "edit": "edit", "delete": "delete"}[job.kind]
        r = FlowRunner(w.page, flow, values, view=w.view, pace_ms=settings.youtube.step_pause_ms, parent=self)
        r.step_started.connect(lambda s, j=job: self._on_step(j, s))
        r.status_text.connect(lambda t, j=job: self._on_status(j, t))
        r.percent.connect(lambda p, j=job: self._on_percent(j, p))
        r.link_found.connect(lambda y, j=job: self._on_link(j, y))
        r.needs_user.connect(lambda why, j=job: self._on_needs_user(j, why))
        r.finished.connect(lambda res, j=job: self._on_flow_finished(j, res))
        r.failed.connect(lambda step, reason, kind, j=job: self._on_flow_failed(j, step, reason, kind))
        self._runner = r
        self.changed.emit()
        r.start()

    @staticmethod
    def _running_text(job: Job) -> str:
        verb = {"upload": "Uploading", "edit": "Updating", "delete": "Deleting"}[job.kind]
        return f"{verb} “{job.label()}” on YouTube. Hiding this window doesn't stop it."

    # ------------------------------------------------------------------ runner signals

    def _on_step(self, job: Job, step: str) -> None:
        job.step = step
        self.changed.emit()

    def _on_status(self, job: Job, text: str) -> None:
        job.status = text
        data = steps_module.load()
        if steps_module.matches(text, steps_module.patterns(data, "processed")):
            job.processed_seen = True
        if job.kind == "upload" and steps_module.matches(text, steps_module.patterns(data, "uploaded")) \
                and not steps_module.matches(text, steps_module.patterns(data, "uploading")):
            v = library.get_video(job.video_id)
            if v.upload_state == "uploading":
                library.mark_upload_state(job.video_id, "processing")
        self.changed.emit()

    def _on_percent(self, job: Job, pct: int) -> None:
        job.percent = pct
        self.changed.emit()

    def _on_link(self, job: Job, yt_id: str) -> None:
        job.yt_id = yt_id
        if job.kind == "upload":
            library.mark_upload_state(job.video_id, library.get_video(job.video_id).upload_state or "uploading",
                                      pending_id=yt_id)
        self.changed.emit()

    def _on_needs_user(self, job: Job, why: str) -> None:
        if why == "review":
            job.state = "waiting_user"
            w = self.window()
            w.set_banner(f"Review “{job.label()}”, then press Save in Studio -- afterglow takes it "
                         "from there.", ("cancel", "hide"))
            w.pop_out()
            self.changed.emit()

    def _on_flow_finished(self, job: Job, result: dict) -> None:
        self._runner = None
        if job.state == "waiting_user":
            job.state = "running"
        if job.kind == "upload":
            yt = result.get("video_id") or job.yt_id
            if not yt:
                self._on_flow_failed(job, "link", "Studio never showed the new video's link.", "error")
                return
            job.yt_id = yt
            self._begin_verify(job)
        elif job.kind == "edit":
            library.set_youtube_metadata(job.video_id, title=job.title, description=job.description)
            self._finish(job, "done")
        elif job.kind == "delete":
            library.forget_youtube(job.video_id)
            self._finish(job, "done")
        self._release_window()

    def _on_flow_failed(self, job: Job, step: str, reason: str, kind: str) -> None:
        self._runner = None
        if kind == "cancelled":
            return                                     # _resolve_current already finished it
        job.state, job.error, job.step = "waiting_user", reason, step
        settings = config_module.load_readonly()
        if job.kind == "upload":
            library.mark_upload_state(job.video_id, "failed", error=reason)
            indicator_client.emit(job.cid, "upload_fail")
            job.cid = None
            self._heartbeat_check()
        self._play(settings.youtube.upload_error_sound)
        w = self.window()
        what = {"upload": "The upload", "edit": "The update", "delete": "Deleting it"}[job.kind]
        if kind == "signed_out":
            text = (f"{what} of “{job.label()}” needs you to be signed in to YouTube. Sign in here, then "
                    "press Retry.")
        else:
            text = (f"{what} of “{job.label()}” stopped at “{step}”: {reason}\n"
                    "Finish it here by hand and press “I finished it here”, or Retry / Cancel. "
                    "The local file is untouched.")
        w.set_banner(text, ("retry", "finished", "cancel", "hide"))
        w.pop_out()
        self.changed.emit()

    # ------------------------------------------------------------------ the banner

    def _on_banner_action(self, action: str) -> None:
        if action == "hide":
            self.window().tuck_away()
            return
        self._resolve_current(action)

    def _resolve_current(self, action: str) -> None:
        job = self.current
        if job is None:
            return
        if action == "retry":
            if self._runner is not None:
                self._runner.cancel("Retrying")
                self._runner = None
            job.state = "queued"
            self.current = None
            self._start(job)
        elif action == "finished":
            self._finished_by_hand(job)
        elif action == "cancel":
            if self._runner is not None:
                r, self._runner = self._runner, None
                r.cancel("Cancelled")
            if job.kind == "upload":
                library.cancel_upload_record(job.video_id)
                if job.cid:
                    indicator_client.emit(job.cid, "upload_fail")
                    job.cid = None
            self._finish(job, "cancelled")
            self._release_window()
            self.window().tuck_away()

    def _finished_by_hand(self, job: Job) -> None:
        """The user completed the step(s) in the window.  For an upload the video's id is needed:
        the one Studio showed, else read off the page now, else asked for."""
        if self._runner is not None:
            r, self._runner = self._runner, None
            r.cancel("Finished by hand")
        if job.kind != "upload":
            if job.kind == "delete":
                library.forget_youtube(job.video_id)
            elif job.kind == "edit":
                library.set_youtube_metadata(job.video_id, title=job.title, description=job.description)
            self._finish(job, "done")
            self._release_window()
            self.window().tuck_away()
            return

        def with_id(found: "str | None") -> None:
            yt = parse_video_id(found or "") or job.yt_id
            if not yt:
                from .themed_dialogs import ask_text
                pasted = ask_text(self.window(), "Video link", "Paste the uploaded video's link (from Studio's "
                                  "“Video link”):", placeholder="https://youtu.be/...", ok_label="Register")
                yt = parse_video_id(pasted or "")
            if not yt:
                return                               # nothing to register: the banner stays up
            job.yt_id = yt
            job.state = "running"
            self._begin_verify(job, by_hand=True)
            self._release_window()
            self.window().tuck_away()

        data = steps_module.load()
        sels = steps_module.selectors(data, "video_link") + steps_module.selectors(data, "after_save_link")
        import json
        from PySide6.QtWebEngineCore import QWebEngineScript
        from ..youtube.studio import _helper_js
        code = _helper_js() + f"\n;window.__ag.href({json.dumps(sels)})"
        self.window().page.runJavaScript(code, QWebEngineScript.ApplicationWorld,
                                         lambda res: QTimer.singleShot(0, lambda: with_id(res if isinstance(res, str) else "")))

    def _release_window(self) -> None:
        """The page is free: the next job may use it."""
        self.current = None
        w = self.window()
        if not any(j.state in ("queued", "running", "waiting_user") for j in self.jobs):
            w.set_busy(False)
            w.set_banner("", ())
            if not w.is_on_screen():
                w.hide()
        QTimer.singleShot(0, self._kick)

    # ------------------------------------------------------------------ verifying (off the GUI thread)

    def _begin_verify(self, job: Job, by_hand: bool = False) -> None:
        job.state = "verifying"
        library.mark_upload_state(job.video_id, "processing", pending_id=job.yt_id)
        # the local thumbnail is the card's art until YouTube's own exists (and stays, for private)
        local_thumb = ""
        try:
            from .. import thumbnails
            from ..youtube.remote import CACHE_DIR
            v = library.get_video(job.video_id)
            t = thumbnails.get_thumbnail(v.id, Path(v.path))
            if t is not None:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                dest = CACHE_DIR / f"{job.yt_id}-local.jpg"
                shutil.copyfile(t, dest)
                local_thumb = str(dest)
        except Exception:  # noqa: BLE001 -- a thumbnail never stops an upload
            local_thumb = ""
        job._local_thumb = local_thumb  # type: ignore[attr-defined]
        self.changed.emit()
        privacy, yt, key, processed = job.privacy, job.yt_id, job.key, job.processed_seen

        def work():
            from ..youtube import remote
            if privacy == "private":
                # not visible from outside: Studio's own "processed" status is all there is
                self._bridge.verified.emit(key, bool(processed or by_hand), "", "" if processed else
                                           "YouTube can't confirm a private video from outside; the local file was kept.")
                return
            deadline = time.monotonic() + float(_verify_timeout())
            ok = False
            while time.monotonic() < deadline:
                if remote.oembed(yt):
                    ok = True
                    break
                time.sleep(_verify_interval())
            thumb = None
            if ok:
                for _ in range(THUMB_TRIES):
                    thumb = remote.fetch_thumbnail(yt)
                    if thumb is not None:
                        break
                    time.sleep(min(_verify_interval(), 10))
            self._bridge.verified.emit(key, ok, str(thumb) if thumb else "",
                                       "" if ok else "YouTube didn't confirm the video plays yet; the local file was kept.")

        threading.Thread(target=work, name="afterglow-yt-verify", daemon=True).start()

    def _on_verified(self, key: int, ok: bool, thumb: str, note: str) -> None:
        job = next((j for j in self.jobs if j.key == key), None)
        if job is None or job.finished:
            return
        settings = config_module.load_readonly()
        thumb_path = thumb or getattr(job, "_local_thumb", "") or None
        library.mark_upload_done(job.video_id, job.yt_id, thumb_path)
        job.error = note
        if ok and settings.youtube.delete_local_after_upload:
            try:
                library.trash_local_file(job.video_id)
            except OSError as e:
                job.error = f"Uploaded, but the local file couldn't be moved to the trash: {e}"
        indicator_client.emit(job.cid, "upload_done")
        job.cid = None
        self._play(settings.youtube.upload_done_sound)
        self._finish(job, "done")

    # ------------------------------------------------------------------ bookkeeping

    def _finish(self, job: Job, state: str) -> None:
        job.state = state
        self.job_finished.emit(job)
        self._heartbeat_check()
        self.changed.emit()
        self._maybe_idle()

    def _maybe_idle(self) -> None:
        if not self.busy():
            self._heartbeat.stop()
            self.idle.emit()

    def _heartbeat_check(self) -> None:
        if not any(j.cid for j in self.jobs if not j.finished):
            self._heartbeat.stop()

    def _beat(self) -> None:
        for j in self.jobs:
            if j.cid and not j.finished:
                indicator_client.emit(j.cid, "upload_progress")

    @staticmethod
    def _play(sound: str) -> None:
        if not sound:
            return
        try:
            from ..clips import play_sound
            play_sound(sound)
        except Exception:  # noqa: BLE001
            pass

    def _recover_interrupted(self) -> None:
        """Uploads a previous run left mid-way (the app was closed / crashed) are failed, never lost:
        the clip keeps its local file and offers Upload again."""
        try:
            for v in library.list_upload_states(("queued", "uploading", "processing")):
                library.mark_upload_state(v.id, "failed", error="afterglow was closed before this upload finished.")
        except Exception:  # noqa: BLE001
            pass


def _verify_timeout() -> float:
    import os
    return float(os.environ.get("AFTERGLOW_YT_VERIFY_TIMEOUT", VERIFY_TIMEOUT))


def _verify_interval() -> float:
    import os
    return float(os.environ.get("AFTERGLOW_YT_VERIFY_INTERVAL", VERIFY_INTERVAL))


_queue: "UploadQueue | None" = None


def upload_queue():
    """The queue as this process sees it: the real UploadQueue inside the YouTube host process (it
    installs one with set_upload_queue), a RemoteUploadQueue client everywhere else (the GUI)."""
    global _queue
    if _queue is None:
        from .youtube_client import RemoteUploadQueue
        _queue = RemoteUploadQueue()
    return _queue


def set_upload_queue(q: "UploadQueue | None") -> None:
    """Tests: install a queue (with a custom window factory) or reset."""
    global _queue
    _queue = q

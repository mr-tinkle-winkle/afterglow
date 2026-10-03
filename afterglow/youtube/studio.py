"""
The YouTube Studio driver: runs one flow from studio_steps.json (upload / edit / delete / account)
against a Studio page, step by step, at a human pace.

No widgets in the logic: ``FlowRunner`` drives a ``StudioPage`` (a QWebEnginePage) and needs a view
only to deliver the one real mouse click Chromium insists on before it opens a file picker
(``real_click_file``).  The view lives in gui/studio_window.py, which keeps it "shown" but never
mapped on screen (Qt.WA_DontShowOnScreen) while an upload runs hidden, so Chromium treats the page
as visible (no background throttling, layout and clicks work) and pops it out on request.

The flow is a Python generator: each step yields small commands -- run this JS, sleep, navigate,
click here -- and the runner resumes it with the result, so a step reads top to bottom like the
clicks a person would make.  Every wait has a timeout; a step that times out (and isn't optional)
stops the run with ``failed(step_id, reason)`` and the window is shown where it stopped.

The one file picker: ``StudioPage.chooseFiles`` answers Studio's picker with the clip's path
(no OS dialog ever appears), but only while armed for that click.
"""
from __future__ import annotations

import json
import random
import time
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QTimer, Qt, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineScript

from . import parse_video_id, steps as steps_module
from .profile import profile, SIGNIN_HOSTS

POLL_MS = 250
ERROR_GRACE_S = 30.0          # an error status must persist this long before the run gives up


def _helper_js() -> str:
    return resources.files(__package__).joinpath("studio_js.js").read_text("utf-8")


class FlowError(Exception):
    def __init__(self, step_id: str, reason: str, kind: str = "error"):
        super().__init__(reason)
        self.step_id, self.reason, self.kind = step_id, reason, kind


class StudioPage(QWebEnginePage):
    """A page in the shared YouTube profile that answers file pickers from code."""

    def __init__(self, parent=None):
        super().__init__(profile(), parent)
        self._armed: "list[str] | None" = None
        self.picker_calls = 0

    def arm_files(self, paths: "list[str] | None") -> None:
        self._armed = list(paths) if paths else None

    def chooseFiles(self, mode, old_files, accepted_mime_types):  # noqa: N802 (Qt name)
        self.picker_calls += 1
        if self._armed:
            files, self._armed = self._armed, None          # one picker per arming
            return files
        return []                                            # never an OS dialog behind the user's back

    def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
        pass                                                 # Studio is chatty; keep the log clean

    def javaScriptAlert(self, url, msg):  # noqa: N802 -- a blocking alert would wedge the flow
        pass

    def javaScriptConfirm(self, url, msg):  # noqa: N802 -- "leave site?" etc: stay
        return False


class FlowRunner(QObject):
    """Runs one named flow.  Values the steps read: title, description, playlist, privacy, file,
    video_id, review (bool).  Signals carry everything the queue / UI need."""
    step_started = Signal(str)                 # step id
    status_text = Signal(str)                  # Studio's own progress text, as shown
    percent = Signal(int)                      # upload progress 0..100 (when Studio shows one)
    link_found = Signal(str)                   # the new video's id (as soon as Studio shows it)
    needs_user = Signal(str)                   # "review": stopped before Save for the user
    finished = Signal(dict)                    # {"video_id": .., "account": ..}
    failed = Signal(str, str, str)             # step id, reason, kind ("signed_out" | "error" | "cancelled")

    def __init__(self, page: StudioPage, flow: str, values: dict, view=None, data: "dict | None" = None,
                 pace_ms: int = 700, parent=None):
        super().__init__(parent)
        self.page = page
        self.view = view
        self.data = data or steps_module.load()
        self.flow_name = flow
        self.values = dict(values)
        self.pace_ms = max(0, int(pace_ms))
        self.result: dict = {}
        self.current_step = ""
        self._gen = None
        self._alive = False
        self._token = 0                       # bumps on cancel: late JS callbacks are ignored
        self._helper = _helper_js()
        self._load_waiter = None
        self.page.loadFinished.connect(self._on_load_finished)

    # ------------------------------------------------------------------ control

    def start(self) -> None:
        self._alive = True
        self._token += 1
        self._gen = self._run()
        QTimer.singleShot(0, lambda t=self._token: self._resume(t, None))

    def cancel(self, reason: str = "Cancelled") -> None:
        if not self._alive:
            return
        self._alive = False
        self._token += 1
        try:
            if self._gen is not None:
                self._gen.close()
        except Exception:  # noqa: BLE001
            pass
        self.page.arm_files(None)
        self.failed.emit(self.current_step, reason, "cancelled")

    def is_running(self) -> bool:
        return self._alive

    # ------------------------------------------------------------------ the scheduler

    def _resume(self, token: int, value) -> None:
        if token != self._token or not self._alive:
            return
        try:
            cmd = self._gen.send(value)
        except StopIteration:
            self._alive = False
            self.finished.emit(dict(self.result))
            return
        except FlowError as e:
            self._alive = False
            self.page.arm_files(None)
            self.failed.emit(e.step_id, e.reason, e.kind)
            return
        except Exception as e:  # noqa: BLE001 -- a bug must surface as a failed step, not a hang
            self._alive = False
            self.page.arm_files(None)
            self.failed.emit(self.current_step, f"internal error: {e!r}", "error")
            return
        self._dispatch(token, cmd)

    def _dispatch(self, token: int, cmd) -> None:
        kind = cmd[0]
        if kind == "js":
            code = self._helper + "\n;JSON.stringify(" + cmd[1] + ");"

            def got(res, t=token):
                try:
                    val = json.loads(res) if isinstance(res, str) else res
                except (TypeError, ValueError):
                    val = None
                QTimer.singleShot(0, lambda: self._resume(t, val))
            self.page.runJavaScript(code, QWebEngineScript.ApplicationWorld, got)
        elif kind == "sleep":
            QTimer.singleShot(int(cmd[1]), lambda t=token: self._resume(t, None))
        elif kind == "navigate":
            self._load_waiter = token
            self.page.load(QUrl(cmd[1]))
            # loadFinished resumes; a load that never finishes is caught by the step's own polling
            QTimer.singleShot(int(cmd[2]) if len(cmd) > 2 else 45000,
                              lambda t=token: self._load_timeout(t))
        elif kind == "click_at":
            ok = self._real_click(cmd[1], cmd[2])
            QTimer.singleShot(0, lambda t=token: self._resume(t, ok))
        else:
            QTimer.singleShot(0, lambda t=token: self._resume(t, None))

    def _on_load_finished(self, ok: bool) -> None:
        t, self._load_waiter = self._load_waiter, None
        if t is not None:
            QTimer.singleShot(0, lambda: self._resume(t, bool(ok)))

    def _load_timeout(self, token: int) -> None:
        if self._load_waiter == token:
            self._load_waiter = None
            self._resume(token, False)

    def _real_click(self, x: float, y: float) -> bool:
        """A genuine (trusted) mouse click at page CSS coordinates: Chromium only opens a file
        picker on real user activation, which a JS .click() doesn't carry."""
        if self.view is None:
            return False
        from PySide6.QtTest import QTest
        target = self.view.focusProxy() or self.view
        zoom = self.page.zoomFactor() or 1.0
        pos = QPoint(int(round(x * zoom)), int(round(y * zoom)))
        QTest.mouseMove(target, pos)
        QTest.mouseClick(target, Qt.LeftButton, Qt.NoModifier, pos)
        return True

    # ------------------------------------------------------------------ yieldables

    @staticmethod
    def _call(fn: str, *args) -> tuple:
        return ("js", f"window.__ag.{fn}({', '.join(json.dumps(a) for a in args)})")

    def _sels(self, name: str) -> "list[str]":
        return steps_module.selectors(self.data, name, PRIVACY=str(self.values.get("privacy", "unlisted")).upper())

    def _pause(self, factor: float = 1.0):
        if self.pace_ms:
            jitter = random.uniform(0.85, 1.25)
            yield ("sleep", int(self.pace_ms * factor * jitter))

    def _wait_visible(self, step: dict, sel_name: str, timeout_ms: int):
        sels = self._sels(sel_name)
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            if (yield self._call("visible", sels)):
                return True
            yield from self._check_signed_out(step)
            if time.monotonic() >= deadline:
                raise FlowError(step["id"], f"'{sel_name}' never appeared ({', '.join(sels[:2])}...)")
            yield ("sleep", POLL_MS)

    def _check_signed_out(self, step: dict):
        host = yield self._call("host")
        if host in SIGNIN_HOSTS or host in self.data.get("signin_hosts", []):
            raise FlowError(step["id"], "Not signed in to YouTube (Settings > YouTube > Sign in).", "signed_out")

    # ------------------------------------------------------------------ the flow

    def _run(self):
        for step in steps_module.flow(self.data, self.flow_name):
            self.current_step = step["id"]
            self.step_started.emit(step["id"])
            handler = getattr(self, "_step_" + step["kind"], None)
            if handler is None:
                raise FlowError(step["id"], f"unknown step kind '{step['kind']}' in studio_steps.json")
            try:
                yield from handler(step)
            except FlowError as e:
                if step.get("optional") and e.kind == "error":
                    continue
                raise

    def _timeout(self, step: dict) -> int:
        return int(step.get("timeout", 20000))

    # each _step_<kind> is a generator

    def _step_open(self, step: dict):
        key = step.get("url", "upload_url")
        url = steps_module.url(self.data, key, video_id=self.values.get("video_id", ""))
        yield ("navigate", url, self._timeout(step))
        deadline = time.monotonic() + self._timeout(step) / 1000.0
        studio_hosts = self.data.get("studio_hosts", ["studio.youtube.com"])
        while True:
            host = yield self._call("host")
            if host in SIGNIN_HOSTS or host in self.data.get("signin_hosts", []):
                raise FlowError(step["id"], "Not signed in to YouTube (Settings > YouTube > Sign in).", "signed_out")
            if host in studio_hosts:
                break
            if time.monotonic() >= deadline:
                raise FlowError(step["id"], f"YouTube Studio didn't open (ended at {host or 'nothing'}).")
            yield ("sleep", POLL_MS)
        yield from self._pause(1.5)

    def _step_wait(self, step: dict):
        yield from self._wait_visible(step, step["sel"], self._timeout(step))

    def _step_wait_gone(self, step: dict):
        sels = self._sels(step["sel"])
        deadline = time.monotonic() + self._timeout(step) / 1000.0
        while (yield self._call("visible", sels)):
            if time.monotonic() >= deadline:
                raise FlowError(step["id"], f"'{step['sel']}' did not close")
            yield ("sleep", POLL_MS)

    def _step_click(self, step: dict):
        if step["id"] == "save" and self.result.get("saved_by_user"):
            return                                      # the user pressed Save after reviewing
        yield from self._wait_visible(step, step["sel"], self._timeout(step))
        deadline = time.monotonic() + self._timeout(step) / 1000.0
        while not (yield self._call("click", self._sels(step["sel"]))):
            if time.monotonic() >= deadline:            # visible but disabled the whole time
                raise FlowError(step["id"], f"'{step['sel']}' stayed disabled")
            yield ("sleep", POLL_MS)
        yield from self._pause()

    def _step_next(self, step: dict):
        yield from self._step_click(step)

    def _step_radio(self, step: dict):
        yield from self._wait_visible(step, step["sel"], self._timeout(step))
        sels = self._sels(step["sel"])
        for _ in range(3):
            yield self._call("click", sels)
            yield ("sleep", 300)
            if (yield self._call("checked", sels)):
                yield from self._pause()
                return
        raise FlowError(step["id"], f"couldn't select '{step['sel']}'"
                        + (f" ({self.values.get('privacy')})" if step.get("privacy") else ""))

    def _step_real_click_file(self, step: dict):
        path = self.values.get("file")
        if not path or not Path(path).exists():
            raise FlowError(step["id"], f"the clip's file is missing: {path}")
        then = step.get("then")
        for attempt in range(2):
            yield from self._wait_visible(step, step["sel"], self._timeout(step))
            rect = yield self._call("rect", self._sels(step["sel"]))
            if not rect:
                continue
            yield ("sleep", 400)                        # let scrollIntoView settle
            rect = (yield self._call("rect", self._sels(step["sel"]))) or rect
            before = self.page.picker_calls
            self.page.arm_files([str(Path(path).resolve())])
            yield ("click_at", rect["x"], rect["y"])
            # the picker is answered synchronously inside Chromium's handling of the click
            deadline = time.monotonic() + 4.0
            while self.page.picker_calls == before and time.monotonic() < deadline:
                yield ("sleep", POLL_MS)
            self.page.arm_files(None)
            if self.page.picker_calls == before:
                continue                                 # the click didn't reach the button: try again
            if then:
                yield from self._wait_visible(step, then, self._timeout(step))
            yield from self._pause()
            return
        raise FlowError(step["id"], "Studio's file picker never opened (the click didn't register).")

    def _step_read_link(self, step: dict):
        sels = self._sels(step["sel"])
        deadline = time.monotonic() + self._timeout(step) / 1000.0
        while True:
            href = yield self._call("href", sels)
            vid = parse_video_id(href or "")
            if vid:
                if self.result.get("video_id") != vid:
                    self.result["video_id"] = vid
                    self.link_found.emit(vid)
                return
            if time.monotonic() >= deadline:
                raise FlowError(step["id"], "the video's link didn't appear")
            yield ("sleep", 500)

    @staticmethod
    def _norm(text: str) -> str:
        return "\n".join(line.rstrip() for line in (text or "").replace("\r", "").strip().split("\n"))

    def _step_set_text(self, step: dict):
        yield from self._wait_visible(step, step["sel"], self._timeout(step))
        value = str(self.values.get(step["value"], ""))
        sels = self._sels(step["sel"])
        for _ in range(3):
            got = yield self._call("setText", sels, value)
            if got is not None and self._norm(got) == self._norm(value):
                yield from self._pause()
                return
            yield ("sleep", 500)
        raise FlowError(step["id"], f"the {step['value']} field didn't take the text")

    def _step_playlist(self, step: dict):
        name = str(self.values.get("playlist") or "").strip()
        if not name:
            return
        yield from self._wait_visible(step, "playlist_trigger", self._timeout(step))
        yield self._call("click", self._sels("playlist_trigger"))
        yield from self._wait_visible(step, "playlist_dialog", self._timeout(step))
        yield ("sleep", 500)
        items, labels, boxes = self._sels("playlist_item"), self._sels("playlist_item_label"), self._sels("playlist_item_checkbox")
        state = yield self._call("itemChecked", items, labels, name, boxes)
        if state is None:
            available = yield self._call("itemTexts", items, labels)
            raise FlowError(step["id"], f"no playlist named '{name}' on this channel"
                            + (f" (found: {', '.join(available[:8])})" if available else ""))
        if not state:
            yield self._call("clickItemByText", items, labels, [name], boxes)
            yield ("sleep", 400)
            if not (yield self._call("itemChecked", items, labels, name, boxes)):
                raise FlowError(step["id"], f"couldn't tick the playlist '{name}'")
        yield from self._pause(0.6)
        yield self._call("click", self._sels("playlist_done"))
        yield from self._pause()

    def _step_wait_status(self, step: dict):
        until = steps_module.patterns(self.data, step.get("until", "uploaded"))
        uploading = steps_module.patterns(self.data, "uploading")
        errors = steps_module.patterns(self.data, "error")
        status_sels, error_sels = self._sels("status_text"), self._sels("error_text")
        deadline = time.monotonic() + self._timeout(step) / 1000.0
        error_since = None
        last = None
        while True:
            text = (yield self._call("text", status_sels)) or ""
            err = (yield self._call("text", error_sels)) if (yield self._call("visible", error_sels)) else ""
            if text != last:
                last = text
                self.status_text.emit(text)
            pct = steps_module.percent(self.data, text)
            if pct is not None:
                self.percent.emit(pct)
            bad = err or (text if steps_module.matches(text, errors) else "")
            if bad:
                error_since = error_since or time.monotonic()
                if time.monotonic() - error_since >= ERROR_GRACE_S:
                    raise FlowError(step["id"], f"YouTube reported: {bad.strip()[:200]}")
            else:
                error_since = None
                if steps_module.matches(text, until) and not steps_module.matches(text, uploading):
                    self.percent.emit(100)
                    return
            if not self.result.get("video_id"):
                href = yield self._call("href", self._sels("video_link"))
                vid = parse_video_id(href or "")
                if vid:
                    self.result["video_id"] = vid
                    self.link_found.emit(vid)
            if time.monotonic() >= deadline:
                raise FlowError(step["id"], "the upload didn't finish in time")
            yield ("sleep", 1000)

    def _step_review(self, step: dict):
        if not self.values.get("review"):
            return
        self.needs_user.emit("review")
        # the user presses Save themselves: wait for the upload dialog to close (no timeout)
        while (yield self._call("visible", self._sels("upload_dialog"))):
            yield ("sleep", 500)
        self.result["saved_by_user"] = True

    def _step_menu(self, step: dict):
        yield from self._wait_visible(step, step["sel"], self._timeout(step))
        texts = self.data.get(step.get("texts", ""), []) if isinstance(step.get("texts"), str) else step.get("texts", [])
        hit = yield self._call("clickItemByText", self._sels(step["sel"]), [], texts, [])
        if not hit:
            raise FlowError(step["id"], f"no menu item {texts}")
        yield from self._pause()

    def _step_account_name(self, step: dict):
        yield from self._wait_visible(step, step["sel"], self._timeout(step))
        self.result["account"] = (yield self._call("text", self._sels(step["sel"]))) or ""

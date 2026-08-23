"""
Embedded video preview/playback for the Editor page, via mpv's OpenGL
render API -- deliberately NOT mpv's window-ID ("wid") embedding.

wid-based embedding is an X11-specific mechanism: it works by handing mpv
a raw X11 window ID to draw into. It does not work when Qt is running as
a native Wayland client, because Wayland has no equivalent concept of
"embed into this arbitrary window handle" -- window composition works
fundamentally differently there. Since fixing the earlier Qt
platform-plugin loading issue means this app can now actually run as a
native Wayland client (rather than failing to start at all), wid
embedding is not a safe bet here. mpv's render API sidesteps the question
entirely: it just draws into an OpenGL context Qt hands it, and doesn't
care what's ultimately hosting that context on either backend.

NOTE: the actual on-screen rendering in this widget could not be tested
in the sandbox this was built in (no display/GPU available there) --
what WAS verified directly: mpv's underlying playback control (load,
pause/play, absolute seeking, position/duration tracking via
observe_property) all work correctly using mpv's headless vo=null output,
which exercises everything except the actual GL draw calls in paintGL().
If the video area renders blank on your machine despite controls working,
that narrows the problem specifically to the OpenGL/proc-address wiring
in initializeGL()/paintGL() below, not the playback logic around it.
"""
from __future__ import annotations

from PySide6.QtCore import Signal, QTimer
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget

import mpv


def _get_proc_address(_ctx, name: bytes) -> int:
    glctx = QOpenGLContext.currentContext()
    if glctx is None:
        return 0
    addr = glctx.getProcAddress(name.decode("utf-8"))
    return int(addr) if addr else 0


class MpvVideoWidget(QOpenGLWidget):
    """
    Small GUI-friendly surface over mpv -- editor_page.py talks to this,
    not to mpv's own API directly, so mpv-specific details stay contained
    in one place.
    """

    position_changed = Signal(float)   # current playback position, seconds
    duration_known = Signal(float)     # fires once when duration becomes available
    playback_ended = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(300)

        self._mpv = mpv.MPV(vo="libmpv", loglevel="error")
        self._render_ctx: "mpv.MpvRenderContext | None" = None
        self._duration_reported = False

        # mpv's property-observer callbacks fire on mpv's own internal
        # thread, not Qt's -- QTimer.singleShot(0, ...) bounces them back
        # onto the Qt event loop rather than touching signals/widgets from
        # a foreign thread, which Qt does not support safely.
        self._mpv.observe_property("time-pos", self._on_time_pos)
        self._mpv.observe_property("duration", self._on_duration)
        self._mpv.observe_property("eof-reached", self._on_eof)

    def initializeGL(self) -> None:
        self._render_ctx = mpv.MpvRenderContext(
            self._mpv, "opengl",
            opengl_init_params={"get_proc_address": _get_proc_address},
        )
        self._render_ctx.update_cb = self.update

    def paintGL(self) -> None:
        if self._render_ctx is None:
            return
        factor = self.devicePixelRatioF()
        self._render_ctx.render(
            flip_y=True,
            opengl_fbo={
                "w": int(self.width() * factor),
                "h": int(self.height() * factor),
                "fbo": self.defaultFramebufferObject(),
            },
        )

    # ------------------------------------------------------------ playback control

    def load(self, path: str) -> None:
        self._duration_reported = False
        self._mpv.play(path)
        self._mpv.pause = True  # load paused -- Editor decides whether/when to auto-play

    def play(self) -> None:
        self._mpv.pause = False

    def pause(self) -> None:
        self._mpv.pause = True

    @property
    def is_paused(self) -> bool:
        return bool(self._mpv.pause)

    def seek(self, position_sec: float) -> None:
        self._mpv.seek(position_sec, reference="absolute", precision="exact")

    def shutdown(self) -> None:
        """Call before the widget/app is destroyed -- mpv holds real
        native resources (decoder threads, GL context state) that need
        explicit teardown, not just Python garbage collection."""
        if self._render_ctx is not None:
            self._render_ctx.free()
            self._render_ctx = None
        self._mpv.terminate()

    # ------------------------------------------------------------ mpv callbacks (foreign thread)

    def _on_time_pos(self, _name, value) -> None:
        if value is not None:
            QTimer.singleShot(0, lambda v=value: self.position_changed.emit(v))

    def _on_duration(self, _name, value) -> None:
        if value is not None and not self._duration_reported:
            self._duration_reported = True
            QTimer.singleShot(0, lambda v=value: self.duration_known.emit(v))

    def _on_eof(self, _name, value) -> None:
        if value:
            QTimer.singleShot(0, self.playback_ended.emit)

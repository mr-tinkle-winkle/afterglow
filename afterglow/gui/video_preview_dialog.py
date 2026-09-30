"""
A larger, playable preview of a Library video -- title, favorite,
filters and info alongside a real embedded player (play/pause, volume,
the trim bar, fullscreen, watch speed), opened via a plain left-click
on a card's thumbnail. This is where quick trimming lives; the
"Advanced Editor" button opens the clip in the track editor (the
Editor page). Uses MpvVideoWidget for playback.

Embedded INSIDE MainWindow's own central widget as an overlay
(VideoPreviewOverlay), NOT a separate top-level QDialog -- that was
tried first and reported as broken in exactly the ways a genuinely
separate OS window would be expected to break for this: fully
detached from the main window, click-outside not registering (a modal
dialog can make the OS/window manager swallow those clicks entirely
before the app ever sees them), and nothing stopping two from being
opened at once. An embedded overlay avoids all three by construction:
it's just another child widget in the SAME window, so "click outside"
is a completely normal mousePressEvent on the overlay's own scrim, and
MainWindow tracks the one active overlay itself so a second request
replaces the first rather than stacking. VideoPreviewContent holds all
the actual player UI (unchanged from before); VideoPreviewOverlay wraps
it with a semi-transparent scrim and sizes it proportionally to
whatever window it's embedded in, rather than a fixed pixel size.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QRect, QSize, Signal, QTimer, QPropertyAnimation, QEvent
from PySide6.QtGui import QPainter, QColor, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QSlider, QAbstractButton,
    QSizePolicy, QWidget, QGraphicsOpacityEffect, QStyle, QApplication,
    QLineEdit, QAbstractSpinBox, QPlainTextEdit, QTextEdit,
)

from .press_pulse import PressPulse
from .. import config as config_module
from .. import library
from .theme import Theme, contrast_text
from .outlined_label import OutlinedLabel
from .custom_button import CustomButton
from .custom_line_edit import CustomLineEdit
from .custom_spinbox import CustomDoubleSpinBox
from .custom_checkbox import CustomCheckBox
from .custom_message_dialog import show_message
from .preview_trim_bar import PreviewTrimBar
from .preview_filters_panel import PreviewFiltersPanel
from .mpv_widget import MpvVideoWidget
from .rounded_rect import rounded_rect_path
from .resources import resource_qpixmap
from .video_card import _format_duration, _format_file_size, _format_date, FAVORITE_STAR

# Same tolerance the Editor uses around the trim-end clamp: mpv's last
# decoded frame is often a hair before the nominal end.
_EOF_EPSILON_SEC = 0.15

_TRANSPORT_BTN_SIZE = 40
SEEK_STEP_SEC = 5.0
VOLUME_STEP = 5
_HEADER_BTN_SIZE = 44
# Fixed content size -- reverted per the direct request, after the
# proportional CONTENT_SIZE_FRACTION approach was tried. Same value as
# the last fixed-size version before that (1581x1035, itself 1.15x an
# earlier 1.25x-of-1100x720 chain) -- clamped to fit the overlay's own
# bounds in _layout_content so it still can't overflow a smaller window.
CONTENT_WIDTH = 1581
CONTENT_HEIGHT = 1035       # (no longer a cap: the box is as tall as the video needs)
CONTENT_MIN_WIDTH = 760     # header/transport need this much even for a tall video


def _video_aspect_of(path) -> float:
    """Display aspect (width / height) of a video file; 16:9 if unknown."""
    try:
        import av
        with av.open(str(path)) as c:
            st = c.streams.video[0]
            w, h = st.codec_context.width, st.codec_context.height
            sar = st.sample_aspect_ratio or 1
            if w and h:
                return float(w * sar) / h
    except Exception:
        pass
    return 16 / 9


class _CardBox(QWidget):
    """A plain rounded, card_background()-colored box -- used for both
    the title/info header and the transport "protrusion" below the
    video, matching a Library card's own info-box treatment."""

    def __init__(self, parent=None):
        super().__init__(parent)
        appearance = config_module.load_readonly().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 12
        radius = min(radius, rect.height() / 2, rect.width() / 2) if radius else 0
        if radius:
            painter.fillPath(rounded_rect_path(rect, radius), self._theme.card_background())
        else:
            painter.fillRect(rect, self._theme.card_background())
        painter.end()


class _VideoFrame(QWidget):
    """Wraps the mpv widget with the SAME accent()-colored border a
    Library thumbnail gets by default (see video_card.py's plain,
    non-highlighted video-box fill) -- the border is just this
    widget's own padding (appearance.unedited_selected_border_width on
    all sides), with the accent color painted behind that padding and
    the mpv widget itself covering the center."""

    def __init__(self, parent=None):
        super().__init__(parent)
        appearance = config_module.load_readonly().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        border = appearance.unedited_selected_border_width
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(border, border, border, border)
        self._fit = None      # QSize the previewer wants (see VideoPreviewContent._fit_video_frame)

    def set_fit(self, size) -> None:
        """Pin the frame to `size` (the video-shaped size that fits; the
        row's spacers center it), or None to fill whatever room it gets."""
        if size == self._fit:
            return
        self._fit = size
        if size is None:
            self.setMinimumSize(0, 0)
            self.setMaximumSize(16777215, 16777215)
        else:
            self.setFixedSize(size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 12
        radius = min(radius, rect.height() / 2, rect.width() / 2) if radius else 0
        if radius:
            painter.fillPath(rounded_rect_path(rect, radius), self._theme.accent())
        else:
            painter.fillRect(rect, self._theme.accent())
        painter.end()


class _PlayPauseButton(QAbstractButton):
    """Checked = currently playing (draws two pause bars); unchecked =
    paused (draws a right-pointing play triangle)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pulse = PressPulse(self)  # shared hover/press pulse, see press_pulse.py
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(_TRANSPORT_BTN_SIZE, _TRANSPORT_BTN_SIZE)
        appearance = config_module.load_readonly().appearance
        self._theme = Theme(appearance)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._pulse.apply(painter)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        bg = self._theme.accent()
        if self.underMouse():
            bg = bg.lighter(115)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(rect)

        fg = contrast_text(bg)
        painter.setBrush(fg)
        cx, cy, r = rect.center().x(), rect.center().y(), rect.width() * 0.28
        if self.isChecked():  # playing -> pause bars
            bar_w = r * 0.5
            painter.drawRect(QRectF(cx - r, cy - r, bar_w, 2 * r))
            painter.drawRect(QRectF(cx + r - bar_w, cy - r, bar_w, 2 * r))
        else:  # paused -> play triangle
            path = QPainterPath()
            path.moveTo(cx - r * 0.7, cy - r)
            path.lineTo(cx - r * 0.7, cy + r)
            path.lineTo(cx + r, cy)
            path.closeSubpath()
            painter.drawPath(path)
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class _FullscreenButton(QAbstractButton):
    """Four corner brackets pointing outward (enter fullscreen) or
    inward (exit fullscreen), depending on `checked`."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pulse = PressPulse(self)  # shared hover/press pulse, see press_pulse.py
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(_TRANSPORT_BTN_SIZE, _TRANSPORT_BTN_SIZE)
        appearance = config_module.load_readonly().appearance
        self._theme = Theme(appearance)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._pulse.apply(painter)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        bg = self._theme.accent()
        if self.underMouse():
            bg = bg.lighter(115)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(rect)

        # A FRESH QPen -- painter.pen() right after setPen(Qt.NoPen)
        # would silently still be a no-draw pen regardless of any
        # color/width set on it afterward; see CustomRadioButton's own
        # comment on this exact Qt gotcha (the corner brackets weren't
        # rendering at all because of it).
        pen = QPen(contrast_text(bg))
        pen.setWidthF(2.2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)

        margin = rect.width() * 0.26
        arm = rect.width() * 0.16
        inward = self.isChecked()
        corners = [
            (rect.left() + margin, rect.top() + margin, 1, 1),
            (rect.right() - margin, rect.top() + margin, -1, 1),
            (rect.left() + margin, rect.bottom() - margin, 1, -1),
            (rect.right() - margin, rect.bottom() - margin, -1, -1),
        ]
        for x, y, sx, sy in corners:
            dx, dy = (-sx, -sy) if inward else (sx, sy)
            painter.drawLine(int(x), int(y), int(x + dx * arm), int(y))
            painter.drawLine(int(x), int(y), int(x), int(y + dy * arm))
        painter.end()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class _ClickToSeekSlider(QSlider):
    """A QSlider that jumps DIRECTLY to wherever you click on its track
    (instead of QSlider's own default of moving one page-step toward
    it) AND lets you continue dragging from there -- "you should be
    able to drag the scrubber around, even if you don't click on it
    and rather click on the line," per the direct request. Used for
    both the scrubber and the volume slider. Dragging the handle
    itself is completely unaffected -- this only changes what starting
    a drag somewhere ELSE on the track does.

    Tracks its own `_track_drag_active` state rather than relying on
    QSlider's internal one, since that internal state is only ever set
    up by QSlider's OWN mousePressEvent -- which is deliberately NOT
    called for a track click (calling it would let QSlider's own
    page-step reaction override the direct jump this makes instead).
    sliderPressed/sliderMoved/sliderReleased are emitted manually here
    to exactly mirror what a real handle drag would produce, so
    everything connected to those (in VideoPreviewContent: pausing for
    the duration of a drag, the live scrub preview, the actual seek on
    release) keeps working identically regardless of which kind of
    drag started it."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._track_drag_active = False

    def _value_at(self, pos) -> int:
        fraction = min(1.0, max(0.0, pos.x() / max(1, self.width())))
        return round(self.minimum() + fraction * (self.maximum() - self.minimum()))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            handle_rect = self.style().subControlRect(
                QStyle.CC_Slider, self._style_option(), QStyle.SC_SliderHandle, self
            )
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            if not handle_rect.contains(pos):
                # A click somewhere else on the track -- starts a drag
                # right here, rather than a one-shot jump: sliderPressed
                # (not sliderMoved yet) so anything listening for "a
                # drag just started" (pausing playback, see
                # VideoPreviewContent._on_scrub_start) reacts the same
                # way it would for a handle-originated drag, THEN jump
                # to this position.
                self._track_drag_active = True
                self.sliderPressed.emit()
                self.setValue(self._value_at(pos))
                self.sliderMoved.emit(self.value())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._track_drag_active and (event.buttons() & Qt.LeftButton):
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self.setValue(self._value_at(pos))
            self.sliderMoved.emit(self.value())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._track_drag_active:
            self._track_drag_active = False
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _style_option(self):
        from PySide6.QtWidgets import QStyleOptionSlider
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        return opt


class _VolumeButton(QAbstractButton):
    """A speaker cone + up to two sound-wave arcs. Click toggles mute
    (handled by the dialog, not this button itself)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pulse = PressPulse(self)  # shared hover/press pulse, see press_pulse.py
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(28, 28)
        self._muted = False

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        self._pulse.apply(painter)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(config_module.load_readonly().appearance.card_text_color)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)

        w, h = self.width(), self.height()
        cone = QPainterPath()
        cone.moveTo(w * 0.15, h * 0.38)
        cone.lineTo(w * 0.4, h * 0.38)
        cone.lineTo(w * 0.65, h * 0.15)
        cone.lineTo(w * 0.65, h * 0.85)
        cone.lineTo(w * 0.4, h * 0.62)
        cone.lineTo(w * 0.15, h * 0.62)
        cone.closeSubpath()
        painter.drawPath(cone)

        # Same fresh-QPen fix as _FullscreenButton above -- the arcs/
        # mute-X lines weren't rendering at all before this.
        pen = QPen(color)
        pen.setWidthF(1.8)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        if self._muted:
            painter.drawLine(int(w * 0.7), int(h * 0.3), int(w * 0.9), int(h * 0.7))
            painter.drawLine(int(w * 0.9), int(h * 0.3), int(w * 0.7), int(h * 0.7))
        else:
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(int(w * 0.68), int(h * 0.28), int(w * 0.2), int(h * 0.44), -60 * 16, 120 * 16)
            painter.drawArc(int(w * 0.78), int(h * 0.16), int(w * 0.22), int(h * 0.68), -55 * 16, 110 * 16)
        painter.end()


class VideoPreviewContent(QWidget):
    """The actual player UI -- title/info, video, transport. A plain
    child widget now (see module docstring), sized/positioned entirely
    by whatever parent embeds it (VideoPreviewOverlay, below)."""

    def __init__(self, video: "library.Video", neighbor_provider=None, parent=None):
        super().__init__(parent)
        appearance = config_module.load_readonly().appearance
        self._theme = Theme(appearance)
        self._neighbor_provider = neighbor_provider
        self._duration = 0.0
        self._current_pos = 0.0
        # Trim-range playback bounds -- committed on handle release, like
        # the Editor (not on every intermediate drag step, so the clamp
        # below doesn't jitter mid-drag).
        self._preview_start = 0.0
        self._preview_end = 0.0
        self._awaiting_restart_seek = False
        self._pre_mute_volume = 80
        self._title_edit = None
        self.setAttribute(Qt.WA_TranslucentBackground, True)  # lets this widget's own rounded corners show the scrim behind it

        outer = QVBoxLayout(self)
        self._outer_layout = outer
        self._overlay_visible = True
        outer.setContentsMargins(16, 16, 16, 16)

        # ---- title + info, centered, in one card_background box ----
        self._header_box = _CardBox()
        header_layout = QVBoxLayout(self._header_box)
        header_layout.setContentsMargins(16, 10, 16, 10)
        header_layout.setSpacing(2)

        self._title_label = OutlinedLabel("")
        self._title_label.setStyleSheet("font-size: 26px;")
        self._title_label.setAlignment(Qt.AlignCenter)
        self._title_label.setCursor(Qt.PointingHandCursor)
        self._title_label.setToolTip("Click to rename")
        self._title_label.installEventFilter(self)
        header_layout.addWidget(self._title_label)

        self._info_label = OutlinedLabel("")
        self._info_label.setStyleSheet("font-size: 13px;")
        self._info_label.setAlignment(Qt.AlignCenter)
        # Info line with Favorite + Filters side by side on the right, as
        # the same circular icon buttons a Library card's quick actions
        # use (Filters reuses that exact filters_icon.png). An equal-width
        # spacer on the left keeps the info text centered.
        info_row = QHBoxLayout()
        info_row.addSpacing(2 * _HEADER_BTN_SIZE + 8)
        info_row.addWidget(self._info_label, stretch=1)
        self.favorite_btn = CustomButton()
        self.favorite_btn.set_circular(_HEADER_BTN_SIZE)
        self.favorite_btn.set_fill_color(appearance.card_text_color)
        self.favorite_btn.clicked.connect(self._toggle_favorite)
        info_row.addWidget(self.favorite_btn)
        info_row.addSpacing(8)
        self.filters_btn = CustomButton()
        self.filters_btn.set_circular(_HEADER_BTN_SIZE)
        self.filters_btn.set_fill_color(appearance.card_text_color)
        self.filters_btn.set_icon_pixmap(resource_qpixmap("filters_icon.png"))
        self.filters_btn.setToolTip("Filters")
        self.filters_btn.clicked.connect(self._toggle_filters_panel)
        info_row.addWidget(self.filters_btn)
        header_layout.addLayout(info_row)
        self._filters_panel = None

        outer.addWidget(self._header_box)

        # ---- video, flanked by prev/next, with the same border a
        # Library thumbnail gets ----
        video_row = QHBoxLayout()
        self._video_row = video_row
        self._video_aspect = 16 / 9
        self.prev_btn = CustomButton("\u25c0")  # "◀"
        self.prev_btn.setFixedWidth(40)
        self.prev_btn.clicked.connect(self._go_to_prev)
        video_row.addWidget(self.prev_btn)

        self._video_frame = _VideoFrame()
        self._video_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget = MpvVideoWidget()
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.clicked.connect(self._toggle_play_pause)
        self.video_widget.position_changed.connect(self._on_position_changed)
        self.video_widget.duration_known.connect(self._on_duration_known)
        self.video_widget.playback_ended.connect(self._on_playback_ended)
        # Rounds the VIDEO's own corners to match its frame's border,
        # which was already rounded while the video itself stayed
        # sharp-cornered inside it -- reported directly. MpvVideoWidget
        # is a QOpenGLWidget, so a normal paintEvent-based rounded clip
        # doesn't apply to its own GL-rendered content; setMask() with a
        # QRegion works at the native surface level regardless of how
        # the content was drawn, which is why that's used here instead.
        # Installed as an event filter (QEvent.Resize) rather than
        # touching MpvVideoWidget itself, since that class is shared
        # with the Editor, which was never asked to round its own video
        # corners -- scoping this to the previewer's own instance only.
        self.video_widget.installEventFilter(self)
        self._video_frame._layout.addWidget(self.video_widget)
        # The frame hugs the video (no black bars beside it): its size is
        # capped to the video's aspect in _fit_video_frame and it's centered
        # in whatever room the row has.
        video_row.addStretch(0)
        video_row.addWidget(self._video_frame, stretch=1)
        video_row.addStretch(0)

        self.next_btn = CustomButton("\u25b6")  # "▶"
        self.next_btn.setFixedWidth(40)
        self.next_btn.clicked.connect(self._go_to_next)
        video_row.addWidget(self.next_btn)

        outer.addLayout(video_row, stretch=1)

        # ---- transport, in its own card_background protrusion ----
        self._transport_box = _CardBox()
        # Two rows inside ONE card: the transport row, then the trim row.
        # Same card so fullscreen's floating/auto-hiding controls (which
        # reparent this whole box) carry the trim controls along too.
        transport_outer = QVBoxLayout(self._transport_box)
        transport_outer.setContentsMargins(0, 0, 0, 0)
        transport_outer.setSpacing(0)
        transport_row_widget = QWidget()
        transport = QHBoxLayout(transport_row_widget)
        transport_outer.addWidget(transport_row_widget)
        transport.setContentsMargins(14, 10, 14, 10)

        self.play_pause_btn = _PlayPauseButton()
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        transport.addWidget(self.play_pause_btn)

        self.time_label = QLabel("0:00 / 0:00")
        transport.addWidget(self.time_label)

        # The Editor's own trim timeline replaces the plain scrubber, with
        # identical interactions: left-click/drag seeks, right-click grabs
        # and drags the nearest trim handle (live-seeking to it), and
        # playback is clamped to the trimmed range.
        self.trim_timeline = PreviewTrimBar()
        self.trim_timeline.set_scale(1.0)
        self.trim_timeline.range_changed.connect(self._on_trim_range_changed)
        self.trim_timeline.seek_requested.connect(self._on_trim_seek_requested)
        self.trim_timeline.drag_started.connect(self._on_trim_drag_started)
        self.trim_timeline.drag_finished.connect(self._on_trim_drag_finished)
        slider_style = (
            "QSlider::groove:horizontal { background: " + self._theme.card_background().name()
            + "; height: 6px; border-radius: 3px; }"
            "QSlider::handle:horizontal { background: " + self._theme.accent().name()
            + "; width: 14px; margin: -5px 0; border-radius: 7px; }"
            "QSlider::sub-page:horizontal { background: " + self._theme.accent().name() + "; border-radius: 3px; }"
            # The "ghost" unplayed portion -- ADD-page is everything
            # AFTER the handle, i.e. what's left to play. Left
            # unstyled before, it just showed the plain groove color
            # (nearly indistinguishable from the general dark UI
            # around it), which read as "the playback line just ends
            # wherever you are" rather than visibly continuing as the
            # rest of the timeline. A lighter, semi-transparent
            # overlay makes the remaining duration clearly visible as
            # its own distinct thing.
            "QSlider::add-page:horizontal { background: rgba(255, 255, 255, 70); border-radius: 3px; }"
        )
        transport.addWidget(self.trim_timeline, stretch=1)

        self.volume_btn = _VolumeButton()
        self.volume_btn.clicked.connect(self._toggle_mute)
        transport.addWidget(self.volume_btn)

        self.volume_slider = _ClickToSeekSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        self.volume_slider.setStyleSheet(slider_style)
        transport.addWidget(self.volume_slider)

        transport.addWidget(QLabel("Speed:"))
        self.speed_spin = CustomDoubleSpinBox()
        self.speed_spin.setRange(0.05, 4.0)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setValue(1.0)
        self.speed_spin.setSuffix("x")
        self.speed_spin.setToolTip("Preview-only playback speed")
        self.speed_spin.valueChanged.connect(self.video_widget.set_speed)
        transport.addWidget(self.speed_spin)

        self.fullscreen_btn = _FullscreenButton()
        self.fullscreen_btn.clicked.connect(self._toggle_fullscreen)
        transport.addWidget(self.fullscreen_btn)

        trim_row_widget = QWidget()
        trim_row = QHBoxLayout(trim_row_widget)
        trim_row.setContentsMargins(14, 0, 14, 10)
        self.trim_range_label = QLabel("Start: 0:00   End: 0:00   Selected: 0:00")
        trim_row.addWidget(self.trim_range_label)
        trim_row.addStretch(1)
        self.frame_perfect_checkbox = CustomCheckBox("Frame Perfect Accuracy")
        self.frame_perfect_checkbox.setToolTip(
            "Exact frame-accurate trim (slower, full re-encode). Off uses a "
            "fast keyframe-based trim: the cut may land on the nearest "
            "keyframe instead of exactly where the handle is."
        )
        trim_row.addWidget(self.frame_perfect_checkbox)
        self.undo_edits_btn = CustomButton("Undo Edits")
        self.undo_edits_btn.setToolTip("Restore this clip from its edit backup.")
        self.undo_edits_btn.clicked.connect(self._undo_edits)
        trim_row.addWidget(self.undo_edits_btn)
        self.save_trim_btn = CustomButton("Save Trim")
        self.save_trim_btn.setToolTip("Trim this clip to the selected range (keeps a backup for Undo).")
        self.save_trim_btn.clicked.connect(self._save_trim)
        trim_row.addWidget(self.save_trim_btn)
        # Bottom-right entry point to the full track editor for this clip.
        self.advanced_edit_btn = CustomButton("Advanced Editor")
        self.advanced_edit_btn.setToolTip("Open this clip in the Advanced Editor (tracks, text, transitions...)")
        self.advanced_edit_btn.clicked.connect(lambda: self.advanced_edit_requested.emit(self._video.id))
        trim_row.addWidget(self.advanced_edit_btn)
        transport_outer.addWidget(trim_row_widget)

        outer.addWidget(self._transport_box)

        self._load_video(video)

    # ------------------------------------------------------------ loading a video

    def _load_video(self, video: "library.Video") -> None:
        self._video = video
        appearance = config_module.load_readonly().appearance
        self.setWindowTitle(video.title or "Preview")

        self._refresh_header()

        self._duration = 0.0
        self._current_pos = 0.0
        self._preview_start = 0.0
        self._preview_end = 0.0
        self._update_trim_buttons()
        self.speed_spin.blockSignals(True)
        self.speed_spin.setValue(1.0)
        self.speed_spin.blockSignals(False)
        self.video_widget.set_speed(1.0)
        # The FIRST video ever loaded into a fresh MpvVideoWidget triggers
        # its own internal "reload 150ms later" workaround for a known
        # black-screen bug (see mpv_widget.py's _reload_first_video) --
        # that reload re-issues the play command for the same file,
        # which if playback had ALREADY started (our own explicit
        # play() below) would briefly overlap the original audio with
        # the reload's own restart of it, reported directly as "doubles
        # up on the audio... jarring." Delaying our own play() call
        # past that 150ms window (only on the very first load -- later
        # loads, e.g. via Prev/Next, never trigger that workaround
        # again) means there's only ever ONE play command in flight by
        # the time audio actually starts, not two overlapping ones.
        self._video_aspect = _video_aspect_of(video.path)
        self._fit_video_frame()
        self.aspect_changed.emit()
        is_first_load_ever = not self.video_widget._first_load_done
        self.video_widget.load(video.path)
        self.video_widget.set_volume(self.volume_slider.value())
        if is_first_load_ever:
            QTimer.singleShot(200, self.video_widget.play)
        else:
            self.video_widget.play()
        self.play_pause_btn.setChecked(True)
        self._update_time_label()
        self._update_neighbor_buttons()

    def _refresh_header(self) -> None:
        """Title (with favorite star), info line (tags + duration/size/
        date) and the Favorite button's state, all from self._video."""
        video = self._video
        appearance = config_module.load_readonly().appearance
        display_title = f"{FAVORITE_STAR} {video.title}" if video.favorite else (video.title or "(untitled)")
        self._title_label.setText(display_title)
        self._title_label.set_colors(appearance.card_text_color, appearance.card_text_outline_color,
                                      outline_width=appearance.card_text_outline_width)
        info_parts = []
        if video.tags:
            info_parts.append(", ".join(video.tags))
        info_parts.extend(p for p in (
            _format_duration(video.duration_sec), _format_file_size(video.path), _format_date(video.created_at),
        ) if p)
        self._info_label.setText(" \u2022 ".join(info_parts))
        self._info_label.set_colors(appearance.card_text_color, appearance.card_text_outline_color,
                                     outline_width=appearance.card_text_outline_width * 0.5)
        self.favorite_btn.set_icon_pixmap(resource_qpixmap(
            "favorite_star_icon.png" if video.favorite else "favorite_star_off_icon.png"))
        self.favorite_btn.setToolTip("Unfavorite" if video.favorite else "Favorite")
        if self._filters_panel is not None:
            self._filters_panel.set_video(video.id)

    def _toggle_favorite(self) -> None:
        self._video = library.set_favorite(self._video.id, not self._video.favorite)
        self._refresh_header()

    def _toggle_filters_panel(self) -> None:
        if self._filters_panel is not None:
            self._close_filters_panel()
            return
        panel = PreviewFiltersPanel(self._video.id, parent=self)
        panel.tags_changed.connect(self._on_tags_changed)
        panel.close_requested.connect(self._close_filters_panel)
        self._filters_panel = panel
        self._position_filters_panel()
        panel.show()
        panel.raise_()

    def _position_filters_panel(self) -> None:
        panel = self._filters_panel
        if panel is None:
            return
        panel.adjustSize()
        anchor = self.filters_btn.mapTo(self, self.filters_btn.rect().bottomRight())
        x = max(0, min(anchor.x() - panel.width(), self.width() - panel.width()))
        panel.move(x, anchor.y() + 6)

    def _close_filters_panel(self) -> None:
        if self._filters_panel is not None:
            self._filters_panel.hide()
            self._filters_panel.deleteLater()
            self._filters_panel = None

    def _on_tags_changed(self) -> None:
        self._video = library.get_video(self._video.id)
        panel, self._filters_panel = self._filters_panel, None  # avoid rebuilding the panel mid-toggle
        self._refresh_header()
        self._filters_panel = panel
        self._position_filters_panel()

    def _update_neighbor_buttons(self) -> None:
        if self._neighbor_provider is None:
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return
        prev_video, next_video = self._neighbor_provider(self._video.id)
        self.prev_btn.setEnabled(prev_video is not None)
        self.next_btn.setEnabled(next_video is not None)
        self.prev_btn.setToolTip(prev_video.title if prev_video else "")
        self.next_btn.setToolTip(next_video.title if next_video else "")

    def _go_to_prev(self) -> None:
        if self._neighbor_provider is None:
            return
        prev_video, _next_video = self._neighbor_provider(self._video.id)
        if prev_video is not None:
            self._load_video(prev_video)

    def _go_to_next(self) -> None:
        if self._neighbor_provider is None:
            return
        _prev_video, next_video = self._neighbor_provider(self._video.id)
        if next_video is not None:
            self._load_video(next_video)

    # ------------------------------------------------------------ playback

    def _toggle_play_pause(self) -> None:
        if self.video_widget.is_paused:
            # Same as the Editor: pressing play outside the trimmed range,
            # or at/after its end, restarts from the trim start.
            position = self._current_pos
            if self._preview_end > 0 and (
                position < self._preview_start - _EOF_EPSILON_SEC
                or position >= self._preview_end - _EOF_EPSILON_SEC
            ):
                self.video_widget.seek(self._preview_start)
                self._current_pos = self._preview_start
                self._awaiting_restart_seek = True
                QTimer.singleShot(250, self._clear_restart_seek_guard)
            self.video_widget.play()
        else:
            self.video_widget.pause()
        self.play_pause_btn.setChecked(not self.video_widget.is_paused)

    def _clear_restart_seek_guard(self) -> None:
        self._awaiting_restart_seek = False

    def _on_position_changed(self, position: float) -> None:
        # Ignore stale positions still arriving from before a restart
        # seek (async seek race -- the Editor has the same guard).
        if self._awaiting_restart_seek or self._shut_down:
            return  # (or late position reports arriving after the player was shut down)
        self._current_pos = position
        # Clamp playback to the trimmed range: stop at the end handle.
        if (
            not self.video_widget.is_paused
            and self._preview_end > 0
            and position >= self._preview_end - _EOF_EPSILON_SEC
        ):
            self.video_widget.pause()
            self.video_widget.seek(self._preview_end)
            self._current_pos = self._preview_end
            self.play_pause_btn.setChecked(False)
        self.trim_timeline.set_playhead(self._current_pos)
        self._update_time_label()

    def _on_duration_known(self, duration: float) -> None:
        self._duration = duration
        self.trim_timeline.set_duration(duration)
        self._preview_start = 0.0
        self._preview_end = duration
        self._update_trim_range_label(0.0, duration)
        self._update_time_label()

    def _on_playback_ended(self) -> None:
        self.play_pause_btn.setChecked(False)

    def _update_time_label(self) -> None:
        current = _format_duration(self._current_pos) or "0:00"
        total = _format_duration(self._duration) or "0:00"
        self.time_label.setText(f"{current} / {total}")

    # ------------------------------------------------------------ trim (same behavior as the Editor)

    def _on_trim_drag_started(self) -> None:
        self.video_widget.pause()
        self.play_pause_btn.setChecked(False)

    def _on_trim_range_changed(self, start: float, end: float) -> None:
        self._update_trim_range_label(start, end)
        # Live-seek to whichever handle is being dragged, so the frame
        # under the handle is what's shown while dragging.
        handle = self.trim_timeline.dragging_handle
        if handle == "start":
            self.video_widget.seek(start)
            self._current_pos = start
        elif handle == "end":
            self.video_widget.seek(end)
            self._current_pos = end
        self._update_time_label()

    def _on_trim_drag_finished(self, start: float, end: float) -> None:
        self._preview_start = start
        self._preview_end = end

    def _on_trim_seek_requested(self, position: float) -> None:
        self.video_widget.seek(position)
        self._current_pos = position
        self.trim_timeline.set_playhead(position)
        self._update_time_label()

    def _update_trim_range_label(self, start: float, end: float) -> None:
        fmt = lambda t: _format_duration(t) or "0:00"
        self.trim_range_label.setText(
            f"Start: {fmt(start)}   End: {fmt(end)}   Selected: {fmt(max(0.0, end - start))}"
        )

    def _update_trim_buttons(self) -> None:
        video = getattr(self, "_video", None)
        self.undo_edits_btn.setEnabled(bool(video and video.has_edit and video.backup_path))

    def _save_trim(self) -> None:
        start, end = self.trim_timeline.start, self.trim_timeline.end
        if self._duration <= 0 or end - start <= 0.05:
            show_message(self, "Can't Trim", "Select a longer range first.")
            return
        self.video_widget.pause()
        self.play_pause_btn.setChecked(False)
        try:
            library.apply_trim(self._video.id, start, end,
                               frame_perfect=self.frame_perfect_checkbox.isChecked())
        except Exception as e:  # ffmpeg / file errors -- surface, don't crash the previewer
            show_message(self, "Trim Failed", str(e))
            return
        self._load_video(library.get_video(self._video.id))

    def _undo_edits(self) -> None:
        video = getattr(self, "_video", None)
        if not (video and video.has_edit and video.backup_path):
            return
        self.video_widget.pause()
        try:
            library.undo_edit(video.id)
        except Exception as e:
            show_message(self, "Undo Failed", str(e))
            return
        self._load_video(library.get_video(video.id))

    # ------------------------------------------------------------ volume

    def _on_volume_changed(self, value: int) -> None:
        self.video_widget.set_volume(value)
        self.volume_btn.set_muted(value == 0)

    def _toggle_mute(self) -> None:
        if self.volume_slider.value() > 0:
            self._pre_mute_volume = self.volume_slider.value()
            self.volume_slider.setValue(0)
        else:
            self.volume_slider.setValue(self._pre_mute_volume or 80)

    # ------------------------------------------------------------ fullscreen / lifecycle

    # Emitted instead of calling showFullScreen()/showNormal() directly
    # -- those are OS-level window operations that don't mean anything
    # for a plain embedded child widget anymore. VideoPreviewOverlay
    # listens for this and resizes the content box to fill essentially
    # the whole overlay (vs. the normal CONTENT_SIZE_FRACTION) instead.
    fullscreen_toggled = Signal(bool)
    advanced_edit_requested = Signal(int)
    aspect_changed = Signal()
    _is_expanded = False
    _shut_down = False

    def _toggle_fullscreen(self) -> None:
        self._close_filters_panel()
        self._is_expanded = not self._is_expanded
        self.fullscreen_btn.setChecked(self._is_expanded)
        # ACTUAL OS-level fullscreen, not just filling most of the
        # overlay. self.window() is MainWindow itself (this widget is
        # embedded inside it), so this fullscreens the whole app.
        window = self.window()
        if self._is_expanded:
            # Only the FullScreen bit is toggled; every other state bit
            # (Maximized in particular) is kept, so leaving fullscreen
            # hands the window back in exactly the state it was in.
            # showFullScreen()/showMaximized() were the "Esc drops the
            # app to a small window" bug: on Wayland, fullscreen ->
            # maximized in one step lands un-maximized at the default
            # restored size (reproduced under a Wayland compositor).
            self._prior_window_state = (window.windowState(), window.geometry())
            window.setWindowState(window.windowState() | Qt.WindowFullScreen)
            self._enter_overlay_controls_mode()
        else:
            self._exit_overlay_controls_mode()
            self._restore_window()
        self.fullscreen_toggled.emit(self._is_expanded)
        overlay = self.parentWidget()
        if overlay is not None:
            overlay.setFocus()
            QTimer.singleShot(200, overlay.setFocus)

    _prior_window_state = None

    def _restore_window(self) -> None:
        state, self._prior_window_state = self._prior_window_state, None
        if state is None:
            return
        prior_state, geometry = state
        window = self.window()
        if prior_state & Qt.WindowFullScreen:
            return  # the app itself was already fullscreen -- stay that way
        window.setWindowState(window.windowState() & ~Qt.WindowFullScreen)
        if not (prior_state & Qt.WindowMaximized):
            # Floating window: re-assert its exact size once the
            # compositor has applied the un-fullscreen.
            QTimer.singleShot(150, lambda: window.resize(geometry.size())
                              if not (window.windowState() & (Qt.WindowFullScreen | Qt.WindowMaximized)) else None)

    def _enter_overlay_controls_mode(self) -> None:
        """While actually fullscreen, the header/transport boxes float
        ON TOP of the video (not in their own separate layout slots
        above/below it) and auto-hide after inactivity -- per Max's
        direct request. Reparents them out of the normal QVBoxLayout
        into plain floating children of `self`, positioned via manual
        geometry instead of layout management, since a layout can't
        make two widgets occupy the same screen space the video itself
        already fills.

        ALSO hides the Prev/Next arrows and strips every remaining
        margin/border around the video -- "get rid of the video switch
        arrows and the background... it should be FULLSCREEN... nothing
        from afterglow on screen unless the popups are currently over
        the screen." The content box itself was already resized to
        100% of the window by a previous fix, but the arrows (still
        occupying their own space in video_row) and this widget's own
        16px outer margin and the video frame's own accent border were
        ALL still visibly squeezing the video into what still looked
        like "its own little window" even though the outer box itself
        was already the full screen size."""
        self._outer_layout.removeWidget(self._header_box)
        self._outer_layout.removeWidget(self._transport_box)
        self._header_box.setParent(self)
        self._transport_box.setParent(self)
        self._position_overlay_controls()
        self._header_box.show()
        self._transport_box.show()
        self._header_box.raise_()
        self._transport_box.raise_()

        self.prev_btn.hide()
        self.next_btn.hide()
        self._outer_layout.setContentsMargins(0, 0, 0, 0)
        self._video_frame._layout.setContentsMargins(0, 0, 0, 0)
        self._video_frame.set_fit(None)       # fill the screen (mpv letterboxes)

        self.setMouseTracking(True)
        self._overlay_visible = True
        self._inactivity_timer = QTimer(self)
        self._inactivity_timer.setInterval(2000)
        self._inactivity_timer.setSingleShot(True)
        self._inactivity_timer.timeout.connect(self._hide_overlay_controls)
        self._inactivity_timer.start()

    def _exit_overlay_controls_mode(self) -> None:
        if hasattr(self, "_inactivity_timer") and self._inactivity_timer is not None:
            self._inactivity_timer.stop()
            self._inactivity_timer = None
        self.setMouseTracking(False)
        self._header_box.setParent(None)
        self._transport_box.setParent(None)
        self._outer_layout.insertWidget(0, self._header_box)
        self._outer_layout.addWidget(self._transport_box)
        self._header_box.show()
        self._transport_box.show()
        self._overlay_visible = True

        self.prev_btn.show()
        self.next_btn.show()
        self._outer_layout.setContentsMargins(16, 16, 16, 16)
        border = config_module.load_readonly().appearance.unedited_selected_border_width
        self._video_frame._layout.setContentsMargins(border, border, border, border)
        self._fit_video_frame()

    def _position_overlay_controls(self) -> None:
        header_h = self._header_box.sizeHint().height()
        transport_h = self._transport_box.sizeHint().height()
        self._header_box.setGeometry(0, 0, self.width(), header_h)
        self._transport_box.setGeometry(0, self.height() - transport_h, self.width(), transport_h)

    def _hide_overlay_controls(self) -> None:
        if not self._is_expanded or not self._overlay_visible:
            return
        self._overlay_visible = False
        self._animate_overlay_slide(showing=False)

    def _show_overlay_controls(self) -> None:
        if not self._is_expanded:
            return
        if hasattr(self, "_inactivity_timer") and self._inactivity_timer is not None:
            self._inactivity_timer.start()  # (re)starts the 2s countdown
        if self._overlay_visible:
            return
        self._overlay_visible = True
        self._animate_overlay_slide(showing=True)

    def _animate_overlay_slide(self, showing: bool) -> None:
        """Slides the header UP off the top edge / down into place, and
        the transport DOWN off the bottom edge / up into place -- per
        the direct request for a slide, not a fade or a teleport."""
        header_h = self._header_box.height()
        transport_h = self._transport_box.height()
        header_shown = QRect(0, 0, self.width(), header_h)
        header_hidden = QRect(0, -header_h, self.width(), header_h)
        transport_shown = QRect(0, self.height() - transport_h, self.width(), transport_h)
        transport_hidden = QRect(0, self.height(), self.width(), transport_h)

        self._header_anim = QPropertyAnimation(self._header_box, b"geometry", self)
        self._header_anim.setDuration(220)
        if showing:
            self._header_anim.setStartValue(header_hidden)
            self._header_anim.setEndValue(header_shown)
        else:
            self._header_anim.setStartValue(header_shown)
            self._header_anim.setEndValue(header_hidden)
        # Safety net: explicitly snaps to the exact target geometry once
        # the animation finishes, regardless of whatever the animation's
        # own final tick landed on -- a resize/reposition happening
        # mid-animation (this box's own sizeHint changing, or the
        # window itself resizing) could otherwise leave it slightly off
        # from where it's actually supposed to end up.
        header_target = header_shown if showing else header_hidden
        self._header_anim.finished.connect(lambda: self._header_box.setGeometry(header_target))

        self._transport_anim = QPropertyAnimation(self._transport_box, b"geometry", self)
        self._transport_anim.setDuration(220)
        if showing:
            self._transport_anim.setStartValue(transport_hidden)
            self._transport_anim.setEndValue(transport_shown)
        else:
            self._transport_anim.setStartValue(transport_shown)
            self._transport_anim.setEndValue(transport_hidden)
        transport_target = transport_shown if showing else transport_hidden
        self._transport_anim.finished.connect(lambda: self._transport_box.setGeometry(transport_target))

        self._header_anim.start()
        self._transport_anim.start()

    def mouseMoveEvent(self, event) -> None:
        if self._is_expanded:
            self._show_overlay_controls()
        super().mouseMoveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._is_expanded and self._overlay_visible:
            self._position_overlay_controls()
        self._fit_video_frame()
        self._position_filters_panel()

    def video_chrome(self) -> "tuple[int, int]":
        """(width, height) this box needs around the video frame when not
        fullscreen: margins, the Prev/Next arrows, the header and the
        transport."""
        m = self._outer_layout.contentsMargins()
        sp_v = max(0, self._outer_layout.spacing())
        sp_h = max(0, self._video_row.spacing())
        cw = m.left() + m.right() + self.prev_btn.width() + self.next_btn.width() + 2 * sp_h
        ch = (m.top() + m.bottom() + self._header_box.sizeHint().height()
              + self._transport_box.sizeHint().height() + 2 * sp_v)
        return cw, ch

    def frame_border(self) -> int:
        return self._video_frame._layout.contentsMargins().left()

    def _fit_video_frame(self) -> None:
        """Cap the bordered video frame to the video's own aspect inside the
        room it has, so there's never letterboxing beside/above the video."""
        if self._is_expanded:
            self._video_frame.set_fit(None)
            return
        cw, ch = self.video_chrome()
        b = self.frame_border()
        aw = max(1, self.width() - cw - 2 * b)
        ah = max(1, self.height() - ch - 2 * b)
        ar = self._video_aspect
        vw = min(aw, ah * ar)
        vh = vw / ar
        self._video_frame.set_fit(QSize(int(round(vw)) + 2 * b, int(round(vh)) + 2 * b))

    def changeEvent(self, event) -> None:
        # Hides the controls immediately on losing window focus (not
        # waiting out the 2s inactivity timer) -- per the direct
        # "when the window loses focus."
        if event.type() == QEvent.ActivationChange and self._is_expanded:
            if not self.window().isActiveWindow():
                self._hide_overlay_controls()
        super().changeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        appearance = config_module.load_readonly().appearance
        # No rounding at all while genuinely fullscreen -- a rounded
        # rect for a shape that now fills the ENTIRE screen edge-to-edge
        # would just clip its own corner pixels to the scrim color
        # behind it, which is exactly the "still has the rounded
        # edges... it should be FULL screen" reported directly. Rounded
        # corners make sense for a floating window-within-a-window
        # (the normal, non-fullscreen case), not for something that's
        # supposed to BE the whole screen.
        radius = 0 if self._is_expanded else (
            appearance.rounded_corner_radius if appearance.rounded_corners_enabled else 16
        )
        if radius:
            painter.fillPath(rounded_rect_path(rect, radius), self._theme.library_background())
        else:
            painter.fillRect(rect, self._theme.library_background())
        painter.end()

    def keyPressEvent(self, event) -> None:
        # Escape is NOT handled here -- it bubbles up to
        # VideoPreviewOverlay, which is what actually owns closing (and
        # exiting the "expanded" state first if that's active).
        if event.key() == Qt.Key_Space:
            self._toggle_play_pause()
        elif event.key() in (Qt.Key_Left, Qt.Key_Right):
            # Left/Right: jump 5 s back/forward
            step = -SEEK_STEP_SEC if event.key() == Qt.Key_Left else SEEK_STEP_SEC
            target = max(0.0, min(self._duration or 0.0, self._current_pos + step))
            self._on_trim_seek_requested(target)
        elif event.key() in (Qt.Key_Up, Qt.Key_Down):
            # Up/Down: volume +/- 5 %
            step = VOLUME_STEP if event.key() == Qt.Key_Up else -VOLUME_STEP
            self.volume_slider.setValue(max(0, min(100, self.volume_slider.value() + step)))
        elif event.key() in (Qt.Key_Comma, Qt.Key_Less):
            # Holding the key "nudges many frames in quick succession
            # until let go" comes entirely for free here -- the OS/Qt's
            # own key-repeat mechanism re-delivers keyPressEvent
            # repeatedly (event.isAutoRepeat() is True for those) for
            # as long as a key stays held, so just acting on every
            # delivery (repeat or not) already gives exactly that
            # behavior without needing a separate timer to drive it.
            self.video_widget.frame_back_step()
            self._update_play_pause_button_from_widget()
        elif event.key() in (Qt.Key_Period, Qt.Key_Greater):
            self.video_widget.frame_step()
            self._update_play_pause_button_from_widget()
        else:
            super().keyPressEvent(event)

    def _update_play_pause_button_from_widget(self) -> None:
        # frame_step()/frame_back_step() both pause playback as part of
        # what mpv's own frame-step/frame-back-step commands do -- keep
        # the button's own state in sync with that rather than leaving
        # it showing "playing" while the video is actually now paused.
        self.play_pause_btn.setChecked(not self.video_widget.is_paused)

    def shutdown(self) -> None:
        """Called by VideoPreviewOverlay right before it closes --
        stops mpv playback/cleans up its resources, same as the old
        QDialog's closeEvent used to. Also leaves fullscreen (restoring
        the prior window state) if the overlay is torn down while
        expanded, e.g. replaced by another preview."""
        self._shut_down = True
        if self._is_expanded:
            self._is_expanded = False
            self._restore_window()
        self.video_widget.shutdown()

    def eventFilter(self, obj, event) -> bool:
        # getattr with a default, not self.video_widget directly -- this
        # filter can fire (a layout/show event on the title label) DURING
        # __init__, before video_widget has been constructed yet a few
        # lines later, which crashed outright before this guard.
        if obj is getattr(self, "video_widget", None) and event.type() == QEvent.Resize:
            self._update_video_mask()
        elif obj is self._title_label and event.type() == QEvent.MouseButtonPress:
            self._start_editing_title()
            return True
        return super().eventFilter(obj, event)

    def _start_editing_title(self) -> None:
        """Swaps the title label out for an editable CustomLineEdit in
        the same spot, pre-filled with the current title -- per Max's
        direct request to be able to rename a video right from the
        previewer instead of needing the Editor or a context-menu
        Rename for it."""
        if self._title_edit is not None:
            return  # already editing
        header_layout = self._title_label.parentWidget().layout()
        index = header_layout.indexOf(self._title_label)
        self._title_label.hide()

        self._title_edit = CustomLineEdit(self._video.title or "")
        self._title_edit.setStyleSheet("font-size: 26px;")
        self._title_edit.setAlignment(Qt.AlignCenter)
        self._title_edit.editingFinished.connect(self._commit_title_edit)
        header_layout.insertWidget(index, self._title_edit)
        self._title_edit.setFocus()
        self._title_edit.selectAll()

    def _commit_title_edit(self) -> None:
        """editingFinished fires on BOTH Enter and losing focus (a
        click elsewhere), so this one connection covers committing the
        rename either way -- clicking away is just as valid a way to
        confirm the new title as pressing Enter."""
        if not hasattr(self, "_title_edit") or self._title_edit is None:
            return
        new_title = self._title_edit.text().strip()
        if new_title and new_title != self._video.title:
            self._video = library.rename_video(self._video.id, title=new_title)

        header_layout = self._title_edit.parentWidget().layout()
        index = header_layout.indexOf(self._title_edit)
        header_layout.removeWidget(self._title_edit)
        self._title_edit.deleteLater()
        self._title_edit = None

        display_title = f"{FAVORITE_STAR} {self._video.title}" if self._video.favorite else (self._video.title or "(untitled)")
        self._title_label.setText(display_title)
        header_layout.insertWidget(index, self._title_label)
        self._title_label.show()
        # Neighbor lookups (Prev/Next tooltips) and the header info
        # box aren't affected by a title change alone, so nothing else
        # here needs refreshing.

    def _update_video_mask(self) -> None:
        w, h = self.video_widget.width(), self.video_widget.height()
        if w <= 0 or h <= 0:
            return
        appearance = config_module.load_readonly().appearance
        radius = appearance.rounded_corner_radius if appearance.rounded_corners_enabled else 12
        radius = min(radius, w / 2, h / 2) if radius else 0
        if radius <= 0:
            self.video_widget.clearMask()
            return
        path = rounded_rect_path(QRectF(0, 0, w, h), radius)
        self.video_widget.setMask(QRegion(path.toFillPolygon().toPolygon()))


class VideoPreviewOverlay(QWidget):
    """Wraps VideoPreviewContent with a semi-transparent scrim, sizing
    the content box to a fixed pixel size (CONTENT_WIDTH x
    CONTENT_HEIGHT -- back to a fixed size per the direct request,
    after CONTENT_SIZE_FRACTION's proportional sizing was tried;
    clamped to fit the overlay's own bounds so it can't overflow a
    smaller window) rather than a fraction of whatever window it's
    embedded in. Meant to be a direct CHILD of MainWindow's central
    widget, not a top-level window at all, so clicking the scrim is a
    completely normal mousePressEvent within the SAME window rather
    than needing any cross-window event filter.

    Fades in quickly (FADE_IN_MS) when shown and fades out at a
    normal, more noticeable speed (FADE_OUT_MS) before actually
    closing -- "fade in very quickly as to be responsive... fade out,
    this one can be at normal speed." Both via one QGraphicsOpacityEffect
    on the overlay itself (covers the scrim AND the content box
    together, so they fade as one unit)."""

    closed = Signal()
    FADE_IN_MS = 90
    FADE_OUT_MS = 220

    def __init__(self, video: "library.Video", neighbor_provider=None, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.content = VideoPreviewContent(video, neighbor_provider=neighbor_provider, parent=self)
        self.content.fullscreen_toggled.connect(lambda _expanded: self._layout_content())
        self.content.aspect_changed.connect(self._layout_content)
        self.setFocusPolicy(Qt.StrongFocus)

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade_anim: QPropertyAnimation | None = None

    def _layout_content(self) -> None:
        if self.content._is_expanded:
            # ACTUALLY fullscreen -- 100% of the window, no margin at
            # all. 97% (matching the non-fullscreen case below) was
            # reported directly as still leaving visible padding
            # between the video and the screen edge, which isn't
            # "fullscreen" in any real sense even though the WINDOW
            # itself was already genuinely fullscreen underneath it.
            w, h = self.width(), self.height()
        else:
            # Sized around the video: as wide as CONTENT_WIDTH allows, and as
            # tall as the video needs at that width (up to the window), so the
            # video fills the box edge to edge instead of sitting between
            # black bars. A tall/narrow video that hits the height limit
            # gets a narrower box instead.
            max_w = min(CONTENT_WIDTH, round(self.width() * 0.97))
            max_h = round(self.height() * 0.97)
            cw, ch = self.content.video_chrome()
            b = self.content.frame_border()
            ar = self.content._video_aspect
            vw = max(1.0, min(max_w - cw - 2 * b, (max_h - ch - 2 * b) * ar))
            vh = vw / ar
            w = max(min(CONTENT_MIN_WIDTH, max_w), int(round(vw)) + 2 * b + cw)
            h = min(max_h, int(round(vh)) + 2 * b + ch)
        x = (self.width() - w) // 2
        y = (self.height() - h) // 2
        self.content.setGeometry(x, y, w, h)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_content()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._layout_content()
        self.setFocus()
        self._animate_opacity(0.0, 1.0, self.FADE_IN_MS)
        # While the preview is open, every key goes to it -- not to whatever
        # widget happens to hold focus (after leaving fullscreen, focus used
        # to land back on the page behind, so Space/arrows drove that).
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        et = event.type()
        if et not in (QEvent.ShortcutOverride, QEvent.KeyPress) or not self.isVisible():
            return False
        if not self.window().isActiveWindow():
            return False                    # a dialog opened from the preview has the keys
        fw = QApplication.focusWidget()
        if fw is not None and self.isAncestorOf(fw) and isinstance(
                fw, (QLineEdit, QAbstractSpinBox, QPlainTextEdit, QTextEdit)):
            return False                    # typing into the title / speed box
        if et == QEvent.ShortcutOverride:
            event.accept()                  # keep app shortcuts from firing; the KeyPress follows
            return True
        if obj is not self:
            self.keyPressEvent(event)
        else:
            return False
        return True

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 140))  # semi-transparent scrim over the page behind it
        painter.end()

    def mousePressEvent(self, event) -> None:
        # If a title edit is in progress, commit it explicitly first --
        # same reasoning as VideoCard's own mousePressEvent fix: a
        # click on the scrim is about to tear down this whole overlay
        # (close_overlay(), below), which would otherwise lose an
        # in-progress edit instead of saving it; a click elsewhere in
        # the content box (the video, a button) doesn't naturally
        # shift Qt's own focus away from the still-focused edit either.
        if self.content._title_edit is not None:
            global_pos = self.mapToGlobal(event.pos())
            local_to_edit = self.content._title_edit.mapFromGlobal(global_pos)
            if not self.content._title_edit.rect().contains(local_to_edit):
                self.content._commit_title_edit()

        # A click anywhere on the SCRIM (i.e. not on the content box
        # itself) closes the overlay -- a completely ordinary
        # mousePressEvent now that this lives inside the same window,
        # not the app-wide event-filter workaround the old top-level-
        # window version needed.
        if not self.content.geometry().contains(event.pos()):
            self.close_overlay()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            if self.content._filters_panel is not None:
                self.content._close_filters_panel()
            elif self.content._is_expanded:
                self.content._toggle_fullscreen()
            else:
                self.close_overlay()
        else:
            # Forwards to the content widget's OWN keyPressEvent
            # explicitly -- VideoPreviewOverlay is what actually holds
            # keyboard focus (showEvent calls self.setFocus() on
            # itself, not on self.content), so without this, every key
            # binding VideoPreviewContent handles (Space for play/
            # pause, frame-step nudging) would never actually be
            # reachable at all -- calling super().keyPressEvent()
            # here only reaches QWidget's own default (which does
            # nothing useful with an unhandled key), not the content
            # widget sitting right below this one.
            self.content.keyPressEvent(event)

    def close_overlay(self, immediate: bool = False) -> None:
        """immediate=True skips the fade-out entirely -- used when
        MainWindow is replacing this overlay with a fresh one (a
        second preview request arriving before this one closed), where
        fading the old one out while a new one fades in on top would
        just look like two overlapping scrims rather than a clean
        swap."""
        if immediate:
            self._finish_close()
            return
        self._animate_opacity(self._opacity_effect.opacity(), 0.0, self.FADE_OUT_MS, on_finished=self._finish_close)

    def _finish_close(self) -> None:
        QApplication.instance().removeEventFilter(self)
        self.content.shutdown()
        # hide() immediately, not just deleteLater() -- deleteLater()'s
        # deletion is deferred to the next event-loop pass, so without
        # an explicit hide() first, a rapidly-replaced overlay (e.g.
        # clicking a second card's thumbnail right after the first)
        # would stay visibly on screen for that brief window -- the
        # exact same class of bug already documented once before in
        # this codebase (deleteLater() left stale visible orphaned
        # card widgets on screen from spam-clicking Refresh).
        self.hide()
        self.closed.emit()
        self.deleteLater()

    def _animate_opacity(self, start: float, end: float, duration: int, on_finished=None) -> None:
        if self._fade_anim is not None:
            self._fade_anim.stop()
        anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        anim.setDuration(duration)
        anim.setStartValue(start)
        anim.setEndValue(end)
        if on_finished is not None:
            anim.finished.connect(on_finished)
        self._fade_anim = anim
        anim.start()

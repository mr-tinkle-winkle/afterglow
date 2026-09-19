"""
A larger, playable preview of a Library video -- title, filters, and
info alongside a real embedded player (play/pause, volume, a seek
scrubber, fullscreen, watch speed), opened via a plain left-click on a
card's thumbnail. Distinct from the Editor: this is read-only
playback, no trimming -- reuses MpvVideoWidget (the same embedding
editor_page.py uses) directly rather than re-solving mpv embedding
here.

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

from PySide6.QtCore import Qt, QRectF, QRect, Signal, QTimer, QPropertyAnimation, QEvent
from PySide6.QtGui import QPainter, QColor, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QSlider, QAbstractButton,
    QSizePolicy, QWidget, QGraphicsOpacityEffect, QStyle,
)

from .. import config as config_module
from .. import library
from .theme import Theme, contrast_text
from .outlined_label import OutlinedLabel
from .custom_button import CustomButton
from .custom_line_edit import CustomLineEdit
from .custom_spinbox import CustomDoubleSpinBox
from .mpv_widget import MpvVideoWidget
from .rounded_rect import rounded_rect_path
from .video_card import _format_duration, _format_file_size, _format_date, FAVORITE_STAR

_TRANSPORT_BTN_SIZE = 40
# Fixed content size -- reverted per Max's direct request, after the
# proportional CONTENT_SIZE_FRACTION approach was tried. Same value as
# the last fixed-size version before that (1581x1035, itself 1.15x an
# earlier 1.25x-of-1100x720 chain) -- clamped to fit the overlay's own
# bounds in _layout_content so it still can't overflow a smaller window.
CONTENT_WIDTH = 1581
CONTENT_HEIGHT = 1035


class _CardBox(QWidget):
    """A plain rounded, card_background()-colored box -- used for both
    the title/info header and the transport "protrusion" below the
    video, matching a Library card's own info-box treatment."""

    def __init__(self, parent=None):
        super().__init__(parent)
        appearance = config_module.load().appearance
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
        appearance = config_module.load().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        border = appearance.unedited_selected_border_width
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(border, border, border, border)

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
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(_TRANSPORT_BTN_SIZE, _TRANSPORT_BTN_SIZE)
        appearance = config_module.load().appearance
        self._theme = Theme(appearance)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
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
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(_TRANSPORT_BTN_SIZE, _TRANSPORT_BTN_SIZE)
        appearance = config_module.load().appearance
        self._theme = Theme(appearance)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
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
    """A QSlider that jumps DIRECTLY to wherever you click on its track,
    instead of QSlider's own default of moving one page-step toward the
    click -- used for both the scrubber and the volume slider, per
    Max's direct request ("clicking on spots on the playback line and
    volume line... teleports the player to that part"). Dragging the
    handle itself is completely unaffected -- this only changes what a
    plain click somewhere else on the track does."""

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            handle_rect = self.style().subControlRect(
                QStyle.CC_Slider, self._style_option(), QStyle.SC_SliderHandle, self
            )
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            if not handle_rect.contains(pos):
                # A click somewhere else on the track -- jump directly
                # there. NOT calling super() for this specific case:
                # QSlider's own default mousePressEvent, for a click
                # that didn't land on the handle, does its own separate
                # page-step-based jump, which would silently override
                # the direct jump just made here. A click ON the handle
                # itself (the branch below) still goes through super()
                # completely normally, which is what actually emits
                # sliderPressed/sliderMoved/sliderReleased and sets up
                # proper drag tracking -- skipping THAT unconditionally
                # would have broken ordinary handle-dragging entirely,
                # not just changed what a track click does.
                fraction = pos.x() / max(1, self.width())
                fraction = min(1.0, max(0.0, fraction))
                value = round(self.minimum() + fraction * (self.maximum() - self.minimum()))
                self.setValue(value)
                self.sliderMoved.emit(value)
                # ALSO emit sliderReleased explicitly, right away --
                # reported directly that a track click "flicks it to
                # it but immediately comes back": since super()'s own
                # press handling never ran for this branch, Qt's
                # internal "am I mid-drag" state was never set up
                # either, so the mouse release that naturally follows
                # this click was never recognized as completing a
                # drag -- sliderReleased (which is what actually
                # triggers the real seek, via _on_scrub_end) never
                # fired at all. The slider's VALUE visibly jumped
                # (setValue above), but nothing ever actually seeked,
                # so the next routine position update from mpv -- still
                # playing from the old, unseeked position -- snapped
                # the handle right back. A click-to-seek is
                # conceptually a press-and-release in the same
                # instant, so synthesizing sliderReleased here too
                # (not just sliderMoved) is what makes that instant
                # click actually behave like a completed one.
                self.sliderReleased.emit()
                event.accept()
                return
        super().mousePressEvent(event)

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
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(28, 28)
        self._muted = False

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor(config_module.load().appearance.card_text_color)
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
        appearance = config_module.load().appearance
        self._theme = Theme(appearance)
        self._neighbor_provider = neighbor_provider
        self._duration = 0.0
        self._current_pos = 0.0
        self._seeking = False
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
        header_layout.addWidget(self._info_label)

        outer.addWidget(self._header_box)

        # ---- video, flanked by prev/next, with the same border a
        # Library thumbnail gets ----
        video_row = QHBoxLayout()
        self.prev_btn = CustomButton("\u25c0")  # "◀"
        self.prev_btn.setFixedWidth(40)
        self.prev_btn.clicked.connect(self._go_to_prev)
        video_row.addWidget(self.prev_btn)

        self._video_frame = _VideoFrame()
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
        video_row.addWidget(self._video_frame, stretch=1)

        self.next_btn = CustomButton("\u25b6")  # "▶"
        self.next_btn.setFixedWidth(40)
        self.next_btn.clicked.connect(self._go_to_next)
        video_row.addWidget(self.next_btn)

        outer.addLayout(video_row, stretch=1)

        # ---- transport, in its own card_background protrusion ----
        self._transport_box = _CardBox()
        transport = QHBoxLayout(self._transport_box)
        transport.setContentsMargins(14, 10, 14, 10)

        self.play_pause_btn = _PlayPauseButton()
        self.play_pause_btn.clicked.connect(self._toggle_play_pause)
        transport.addWidget(self.play_pause_btn)

        self.time_label = QLabel("0:00 / 0:00")
        transport.addWidget(self.time_label)

        self.scrubber = _ClickToSeekSlider(Qt.Horizontal)
        self.scrubber.setRange(0, 1000)
        self.scrubber.sliderPressed.connect(self._on_scrub_start)
        self.scrubber.sliderMoved.connect(self._on_scrub_moved)
        self.scrubber.sliderReleased.connect(self._on_scrub_end)
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
        self.scrubber.setStyleSheet(slider_style)
        transport.addWidget(self.scrubber, stretch=1)

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

        outer.addWidget(self._transport_box)

        self._load_video(video)

    # ------------------------------------------------------------ loading a video

    def _load_video(self, video: "library.Video") -> None:
        self._video = video
        appearance = config_module.load().appearance
        self.setWindowTitle(video.title or "Preview")

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

        self._duration = 0.0
        self._current_pos = 0.0
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
            self.video_widget.play()
        else:
            self.video_widget.pause()
        self.play_pause_btn.setChecked(not self.video_widget.is_paused)

    def _on_position_changed(self, position: float) -> None:
        self._current_pos = position
        if not self._seeking and self._duration > 0:
            self.scrubber.blockSignals(True)
            self.scrubber.setValue(round(position / self._duration * 1000))
            self.scrubber.blockSignals(False)
        self._update_time_label()

    def _on_duration_known(self, duration: float) -> None:
        self._duration = duration
        self._update_time_label()

    def _on_playback_ended(self) -> None:
        self.play_pause_btn.setChecked(False)

    def _update_time_label(self) -> None:
        current = _format_duration(self._current_pos) or "0:00"
        total = _format_duration(self._duration) or "0:00"
        self.time_label.setText(f"{current} / {total}")

    # ------------------------------------------------------------ scrubber

    def _on_scrub_start(self) -> None:
        self._seeking = True

    def _on_scrub_moved(self, value: int) -> None:
        if self._duration > 0:
            self._current_pos = value / 1000 * self._duration
            self._update_time_label()

    def _on_scrub_end(self) -> None:
        if self._duration > 0:
            self.video_widget.seek(self.scrubber.value() / 1000 * self._duration)
        self._seeking = False

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
    _is_expanded = False

    def _toggle_fullscreen(self) -> None:
        self._is_expanded = not self._is_expanded
        self.fullscreen_btn.setChecked(self._is_expanded)
        # ACTUAL OS-level fullscreen now, not just filling most of the
        # overlay -- reported directly that it wasn't genuinely
        # fullscreen before. self.window() is MainWindow itself (this
        # widget is embedded inside it, not a separate top-level
        # window), so this fullscreens the whole app window (hiding
        # its own title bar/taskbar presence), with the video content
        # then filling essentially all of that.
        window = self.window()
        if self._is_expanded:
            window.showFullScreen()
            self._enter_overlay_controls_mode()
        else:
            window.showNormal()
            self._exit_overlay_controls_mode()
        self.fullscreen_toggled.emit(self._is_expanded)

    def _enter_overlay_controls_mode(self) -> None:
        """While actually fullscreen, the header/transport boxes float
        ON TOP of the video (not in their own separate layout slots
        above/below it) and auto-hide after inactivity -- per Max's
        direct request. Reparents them out of the normal QVBoxLayout
        into plain floating children of `self`, positioned via manual
        geometry instead of layout management, since a layout can't
        make two widgets occupy the same screen space the video itself
        already fills."""
        self._outer_layout.removeWidget(self._header_box)
        self._outer_layout.removeWidget(self._transport_box)
        self._header_box.setParent(self)
        self._transport_box.setParent(self)
        self._position_overlay_controls()
        self._header_box.show()
        self._transport_box.show()
        self._header_box.raise_()
        self._transport_box.raise_()

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
        Max's direct request for a slide, not a fade or a teleport."""
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

    def changeEvent(self, event) -> None:
        # Hides the controls immediately on losing window focus (not
        # waiting out the 2s inactivity timer) -- per Max's direct
        # "when the window loses focus."
        if event.type() == QEvent.ActivationChange and self._is_expanded:
            if not self.window().isActiveWindow():
                self._hide_overlay_controls()
        super().changeEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        appearance = config_module.load().appearance
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
        QDialog's closeEvent used to."""
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
        appearance = config_module.load().appearance
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
    CONTENT_HEIGHT -- back to a fixed size per Max's direct request,
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
            w = min(CONTENT_WIDTH, round(self.width() * 0.97))
            h = min(CONTENT_HEIGHT, round(self.height() * 0.97))
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
            if self.content._is_expanded:
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

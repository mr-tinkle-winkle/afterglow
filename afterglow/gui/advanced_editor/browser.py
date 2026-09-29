"""
The Advanced Editor's browser panel (top left): Media / Text / Audio /
Transitions / Effects. Items can be double-clicked (added at the
playhead / applied to the selection) or dragged onto the timeline.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import QMimeData, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QDrag, QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QStackedWidget, QVBoxLayout, QWidget, QAbstractItemView,
)

from ... import config as config_module
from ... import library, thumbnails
from ...nle import ops
from ...nle.media import GIF_EXTS, IMAGE_EXTS
from ..custom_button import CustomButton
from ..custom_combo_style import combo_box_stylesheet
from ..custom_scrollbar import CustomScrollBar
from ..custom_spinbox import CustomDoubleSpinBox
from ..segment_button import SegmentButton
from ..theme import Theme
from .controller import TEXT_PRESETS, EditorController
from .timeline import MIME_ITEM

MEDIA_FILTER = ("Media (*.mp4 *.mkv *.mov *.webm *.avi *.flv *.m4v *.ts *.mp3 *.wav *.flac *.ogg *.opus *.m4a *.aac "
                "*.png *.jpg *.jpeg *.webp *.bmp *.gif);;All files (*)")
AUDIO_FILTER = "Audio (*.mp3 *.wav *.flac *.ogg *.opus *.m4a *.aac);;All files (*)"
PICTURE_FILTER = "Pictures and GIFs (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"


class _DragList(QListWidget):
    """A list whose items carry a JSON payload (Qt.UserRole) that becomes
    the drag data for the timeline.

    Starts the drag itself (press on an item, move a few px) instead of
    relying on QListView's built-in drag: in icon mode the built-in one
    turned a press-and-drag into rubber-band selection with auto-scroll,
    so dragging a library clip scrolled the list instead of dragging."""

    def __init__(self, icon_mode: bool = False, parent=None):
        super().__init__(parent)
        self.setDragEnabled(False)
        self.setDragDropMode(QAbstractItemView.NoDragDrop)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setVerticalScrollBar(CustomScrollBar(Qt.Vertical))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setAutoScroll(False)
        self._press_item = None
        self._press_pos = None
        self.drags_enabled = True
        if icon_mode:
            self.setViewMode(QListWidget.IconMode)
            self.setResizeMode(QListWidget.Adjust)
            self.setIconSize(QSize(128, 72))
            self.setGridSize(QSize(140, 104))
            self.setWordWrap(True)
            self.setMovement(QListWidget.Static)
        appearance = config_module.load_readonly().appearance
        self.setStyleSheet(
            f"QListWidget {{ background-color: {appearance.afterglow_color_card_background};"
            f" color: {appearance.card_text_color}; border: none; border-radius: 8px; padding: 4px; }}"
            f"QListWidget::item {{ border-radius: 6px; padding: 4px; }}"
            f"QListWidget::item:selected {{ background-color: {appearance.afterglow_color_accent}; }}")

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        if event.button() == Qt.LeftButton:
            self._press_item = self.itemAt(event.position().toPoint())
            self._press_pos = event.position().toPoint()

    def mouseMoveEvent(self, event) -> None:
        if (self.drags_enabled and self._press_item is not None and event.buttons() & Qt.LeftButton
                and (event.position().toPoint() - self._press_pos).manhattanLength() >= 6):
            item, self._press_item = self._press_item, None
            payload = item.data(Qt.UserRole)
            if callable(payload):
                payload = payload()
            if payload:
                md = QMimeData()
                md.setData(MIME_ITEM, json.dumps(payload).encode())
                drag = QDrag(self)
                drag.setMimeData(md)
                icon = item.icon()
                if not icon.isNull():
                    drag.setPixmap(icon.pixmap(96, 54))
                drag.exec(Qt.CopyAction)
            return
        if self._press_item is None:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._press_item = None
        super().mouseReleaseEvent(event)


class BrowserPanel(QWidget):
    def __init__(self, controller: EditorController, parent=None):
        super().__init__(parent)
        self.ctl = controller
        appearance = config_module.load_readonly().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        self._label_qss = f"QLabel {{ color: {appearance.card_text_color}; }}"
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        tabs = QHBoxLayout()
        tabs.setSpacing(0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.stack = QStackedWidget()
        names = ["Media", "Text", "Audio", "Transitions", "Effects"]
        for i, name in enumerate(names):
            pos = "left" if i == 0 else "right" if i == len(names) - 1 else "middle"
            b = SegmentButton(None, pos, text=name)
            b.setMinimumHeight(30)
            tabs.addWidget(b, stretch=1)
            self._group.addButton(b, i)
        self._group.idClicked.connect(self._on_tab)
        outer.addLayout(tabs)
        outer.addWidget(self.stack, stretch=1)

        self.stack.addWidget(self._media_page())
        self.stack.addWidget(self._text_page())
        self.stack.addWidget(self._audio_page())
        self.stack.addWidget(self._transitions_page())
        self.stack.addWidget(self._effects_page())
        self._group.button(0).setChecked(True)
        self._library_loaded = False
        controller.changed.connect(self._refresh_audio_list)

    def _on_tab(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        if i == 0:
            self.load_library()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self.load_library)

    def _note(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setStyleSheet(self._label_qss + "QLabel { font-size: 11px; }")
        return lab

    # ---- Media -------------------------------------------------------------
    def _media_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        add = CustomButton("Add File…")
        add.setToolTip("Add a video, audio file, picture or GIF at the playhead")
        add.clicked.connect(self._add_files)
        pic = CustomButton("Picture / GIF…")
        pic.clicked.connect(lambda: self._add_files(PICTURE_FILTER))
        refresh = CustomButton("Refresh")
        refresh.clicked.connect(lambda: self.load_library(force=True))
        row.addWidget(add)
        row.addWidget(pic)
        row.addWidget(refresh)
        lay.addLayout(row)
        lay.addWidget(self._note("Library clips -- double-click to add at the playhead, or drag onto the timeline."))
        self.media_list = _DragList(icon_mode=True)
        self.media_list.itemDoubleClicked.connect(self._activate_item)
        lay.addWidget(self.media_list, stretch=1)
        return w

    def load_library(self, force: bool = False) -> None:
        if self._library_loaded and not force:
            return
        self._library_loaded = True
        self.media_list.clear()
        try:
            videos = library.list_videos()
        except Exception:
            videos = []
        placeholder = QPixmap(128, 72)
        placeholder.fill(QColor(self._appearance.afterglow_color_library))
        pending = []
        for v in videos:
            it = QListWidgetItem(QIcon(placeholder), v.title or Path(v.path).stem)
            it.setToolTip(str(v.path))
            it.setData(Qt.UserRole, {"type": "file", "path": str(v.path)})
            self.media_list.addItem(it)
            pending.append((it, v))

        # Thumbnails come from the Library's own disk cache; load a few per
        # event-loop pass so a big library never stalls the UI.
        def step():
            for _ in range(6):
                if not pending:
                    return
                it, v = pending.pop(0)
                try:
                    path = thumbnails.get_thumbnail(v.id, Path(v.path))
                except Exception:
                    path = None
                if path is not None:
                    pm = QPixmap(str(path))
                    if not pm.isNull():
                        it.setIcon(QIcon(pm.scaled(128, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            QTimer.singleShot(0, step)
        QTimer.singleShot(0, step)

    def _add_files(self, file_filter: str = MEDIA_FILTER) -> None:
        if not self.ctl.has_project:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Add to timeline", str(Path.home()), file_filter)
        t = self.ctl.playhead
        for path in paths:
            seg = self.ctl.add_file(path, t=t, track_index=self._default_track(path))
            if seg is not None:
                t = seg.end

    def _default_track(self, path: str):
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_EXTS or ext in GIF_EXTS:
            return 1          # pictures go on the top lane (over the video), kicked up if busy
        return None

    def _activate_item(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.UserRole)
        if callable(payload):
            payload = payload()
        if not payload or not self.ctl.has_project:
            return
        typ = payload.get("type")
        if typ == "file":
            self.ctl.add_file(payload["path"])
        elif typ == "text":
            self.ctl.add_text(payload["preset"])
        elif typ == "transition":
            if not self.ctl.selection:
                self.ctl.error.emit("Select the segment the transition should lead into.")
                return
            self.ctl.apply_transition(list(self.ctl.selection), payload["kind"], payload["duration"],
                                      payload["target"], payload["direction"])
        elif typ == "effect":
            self._apply_effect(payload["name"])

    # ---- Text --------------------------------------------------------------
    def _text_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._note("Double-click or drag a style onto the timeline. Edit the words, font and colors in "
                                 "Properties; move and resize it in the preview."))
        lst = _DragList()
        for name in TEXT_PRESETS:
            it = QListWidgetItem(name)
            it.setData(Qt.UserRole, {"type": "text", "preset": name})
            lst.addItem(it)
        lst.itemDoubleClicked.connect(self._activate_item)
        lay.addWidget(lst, stretch=1)
        return w

    # ---- Audio -------------------------------------------------------------
    def _audio_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        add = CustomButton("Add Audio File…")
        add.clicked.connect(lambda: self._add_files(AUDIO_FILTER))
        det = CustomButton("Detach Audio")
        det.setToolTip("Split the selected video's audio onto its own segment")
        det.clicked.connect(self.ctl.detach_audio)
        row.addWidget(add)
        row.addWidget(det)
        lay.addLayout(row)
        lay.addWidget(self._note("Audio files in this project -- double-click to add another copy at the playhead. "
                                 "Audio can go anywhere on any track."))
        self.audio_list = _DragList()
        self.audio_list.itemDoubleClicked.connect(self._activate_item)
        lay.addWidget(self.audio_list, stretch=1)
        return w

    def _refresh_audio_list(self) -> None:
        p = self.ctl.project
        paths = []
        if p is not None:
            for s in p.all_segments():
                for part in s.parts:
                    if part.has_audio and not part.has_video and part.source and part.source not in paths:
                        paths.append(part.source)
        current = [self.audio_list.item(i).data(Qt.UserRole)["path"] for i in range(self.audio_list.count())]
        if current == paths:
            return
        self.audio_list.clear()
        for path in paths:
            it = QListWidgetItem(os.path.basename(path))
            it.setToolTip(path)
            it.setData(Qt.UserRole, {"type": "file", "path": path})
            self.audio_list.addItem(it)

    # ---- Transitions -------------------------------------------------------
    def _transitions_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        combo_qss = combo_box_stylesheet(self._appearance)
        form = QFormLayout()
        self.t_dur = CustomDoubleSpinBox()
        self.t_dur.setRange(0.05, 30)
        self.t_dur.setSingleStep(0.1)
        self.t_dur.setValue(0.5)
        self.t_dur.setSuffix(" s")
        self.t_target = QComboBox()
        self.t_target.setStyleSheet(combo_qss)
        for label, data in (("Both", "both"), ("Destination", "destination"), ("Original", "original")):
            self.t_target.addItem(label, data)
        self.t_dir = QComboBox()
        self.t_dir.setStyleSheet(combo_qss)
        for label, data in (("Left", "left"), ("Right", "right"), ("Top", "top"), ("Bottom", "bottom")):
            self.t_dir.addItem(label, data)
        for label, widget in (("Duration", self.t_dur), ("Moves", self.t_target), ("From", self.t_dir)):
            lab = QLabel(label)
            lab.setStyleSheet(self._label_qss)
            form.addRow(lab, widget)
        lay.addLayout(form)
        lay.addWidget(self._note("Drop a transition onto the segment it leads INTO (it blends from the segment ending "
                                 "right where that one starts), or select that segment and double-click. "
                                 "Moves/From apply to Slide and Fade."))
        lst = _DragList()
        for label, kind in (("Crossfade", "crossfade"), ("Blur / Focus", "blur"), ("Slide", "slide"),
                            ("Fade (wipe)", "fade")):
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, (lambda k=kind: {"type": "transition", "kind": k,
                                                     "duration": self.t_dur.value(),
                                                     "target": self.t_target.currentData(),
                                                     "direction": self.t_dir.currentData()}))
            lst.addItem(it)
        lst.itemDoubleClicked.connect(self._activate_item)
        lay.addWidget(lst, stretch=1)
        rm = CustomButton("Remove Transition from Selected")
        rm.clicked.connect(lambda: self.ctl.apply_transition(list(self.ctl.selection), None))
        lay.addWidget(rm)
        return w

    # ---- Effects -----------------------------------------------------------
    EFFECTS = [
        ("Punch-in zoom (130%)", "zoom_punch"),
        ("Slow zoom (115%)", "zoom_slow"),
        ("Fade in + out (0.5 s)", "fades"),
        ("Half speed", "speed_half"),
        ("Double speed", "speed_double"),
        ("Normal speed", "speed_normal"),
        ("Remove zoom + fades", "clear"),
    ]

    def _effects_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._note("Double-click to apply to the selected segments. Fine-tune in Properties."))
        lst = _DragList()
        lst.drags_enabled = False
        for label, name in self.EFFECTS:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, {"type": "effect", "name": name})
            lst.addItem(it)
        lst.itemDoubleClicked.connect(self._activate_item)
        lay.addWidget(lst, stretch=1)
        return w

    def _apply_effect(self, name: str) -> None:
        ids = list(self.ctl.selection)
        if not ids:
            self.ctl.error.emit("Select one or more segments first.")
            return

        def fn(p):
            for sid in ids:
                _, s = p.find_segment(sid)
                if s is None or s.locked:
                    continue
                if name == "zoom_punch":
                    ops.set_zoom(p, [sid], 1.3, min(0.4, s.duration / 3), min(0.4, s.duration / 3))
                elif name == "zoom_slow":
                    ops.set_zoom(p, [sid], 1.15, s.duration * 0.999, 0.0)
                elif name == "fades":
                    ops.set_fades(p, [sid], fade_in=0.5, fade_out=0.5)
                elif name == "speed_half":
                    ops.set_speed(p, sid, 0.5)
                elif name == "speed_double":
                    ops.set_speed(p, sid, 2.0)
                elif name == "speed_normal":
                    ops.set_speed(p, sid, 1.0)
                elif name == "clear":
                    ops.set_zoom(p, [sid], 1.0, 0.0, 0.0)
                    ops.set_fades(p, [sid], fade_in=0.0, fade_out=0.0)
        self.ctl.perform("Effect", fn)

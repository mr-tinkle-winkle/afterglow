"""
The Editor page = the Advanced Editor (quick trimming lives in the video
previewer now). Filmora-style layout:

    [Import] [ name ............ ] [● unsaved]            [Undo][Redo] [Save]
    +-----------+---------------------------+--------------+
    | Browser   |         Preview           |  Properties  |
    +-----------+---------------------------+--------------+
    [toolbar: undo redo | split combine | snapping | zoom - fit +]
    [ timeline .............................................. ]

Opening a Library clip (Edit, or "Advanced Editor" in the previewer)
loads its saved project if it has one (edits stay editable), otherwise a
new project holding just the clip. Import opens any file; saving writes
"<name>-edited.<ext>" next to it. Save renders the timeline (same
renderer as the preview) over the clip, keeping the original in Edit
Backups so Undo Edits restores it.

The page keeps the public surface MainWindow already used for the old
Editor page: current_video_id, load_video(), set_neighbor_provider(),
apply_scale().
"""
from __future__ import annotations

import copy
import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QProgressBar, QSplitter,
    QVBoxLayout, QWidget,
)

from ... import config as config_module
from ... import library
from ...nle import save as nle_save
from ...nle.model import Project
from ..custom_button import CustomButton
from ..custom_combo_box import CustomComboBox
from ..custom_combo_style import combo_box_stylesheet
from ..custom_line_edit import CustomLineEdit
from ..custom_message_dialog import ask_confirm, show_message
from ..page_outline import BORDER_WIDTH, paint_page_outline
from ..rounded_rect import rounded_rect_path
from ..theme import Theme
from . import icons
from .browser import BrowserPanel
from .controller import EditorController
from .preview import PreviewPanel
from .properties import PropertiesPanel
from .timeline import TimelinePanel
from .visuals import TimelineVisuals
from ..themed_dialogs import ThemedDialog, TextInputDialog, ask_text, get_open_file_name


class _Panel(QWidget):
    """Rounded card_background box around each area."""

    def __init__(self, inner: QWidget, parent=None):
        super().__init__(parent)
        a = config_module.load_readonly().appearance
        self._appearance = a
        self._theme = Theme(a)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(inner)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        radius = self._appearance.rounded_corner_radius if self._appearance.rounded_corners_enabled else 8
        p.fillPath(rounded_rect_path(r, min(radius, 18)), self._theme.card_background())
        p.end()


class _Dialog(ThemedDialog):
    """Frameless rounded dialog, same look as every other app dialog
    (see themed_dialogs.ThemedDialog)."""


# The name prompt lives in themed_dialogs now (shared with Settings'
# New Category / Rename Filter prompts); kept under the old names here.
NameDialog = TextInputDialog


def ask_name(parent, title: str, text: str, default: str = "", ok_label: str = "Save") -> "str | None":
    return ask_text(parent, title, text, default, ok_label)


class SaveDialog(_Dialog):
    QUALITIES = [("High (larger file)", 18), ("Medium", 23), ("Small file", 28)]

    def __init__(self, project: Project, target_text: str, parent=None, library_clip: bool = False):
        super().__init__("Export", parent)
        self.replace_radio = self.separate_radio = None
        if library_clip:
            from ..custom_radio_button import CustomRadioButton
            from PySide6.QtWidgets import QButtonGroup
            self.replace_radio = CustomRadioButton("Replace this clip")
            self.separate_radio = CustomRadioButton("Save separately (as a new clip)")
            grp = QButtonGroup(self)
            grp.addButton(self.replace_radio)
            grp.addButton(self.separate_radio)
            self.replace_radio.setChecked(True)
            self.lay.addWidget(self.replace_radio)
            self.lay.addWidget(self.label("The original is kept in Edit Backups; Undo Edits restores it."))
            self.lay.addWidget(self.separate_radio)
            self.lay.addWidget(self.label("Adds a new \u201c(edited)\u201d clip to the Library next to this one; "
                                          "this clip stays exactly as it is."))
        else:
            self.lay.addWidget(self.label(target_text))
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        qss = combo_box_stylesheet(self._appearance)
        self.res = CustomComboBox()
        w, h = project.width, project.height
        self.res.addItem(f"Original ({w}×{h})", (w, h))
        for ph in (2160, 1440, 1080, 720, 480):
            if ph < h:
                pw = int(round(ph * w / h / 2) * 2)
                self.res.addItem(f"{ph}p ({pw}×{ph})", (pw, ph))
        self.quality = CustomComboBox()
        for label, crf in self.QUALITIES:
            self.quality.addItem(label, crf)
        for c in (self.res, self.quality):
            c.setStyleSheet(qss)
            # Size from the longest item (+ the stylesheet's padding), so
            # nothing is cut off.
            c.setSizeAdjustPolicy(QComboBox.AdjustToContents)
            longest = max(c.fontMetrics().horizontalAdvance(c.itemText(i)) for i in range(c.count()))
            c.setMinimumWidth(longest + 60)
            c.setMinimumHeight(c.fontMetrics().height() + 14)
        form.addRow(self.label("Resolution"), self.res)
        form.addRow(self.label("Quality"), self.quality)
        self.lay.addLayout(form)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = CustomButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = CustomButton("Export")
        ok.clicked.connect(self.accept)
        for b_ in (cancel, ok):
            b_.setMinimumWidth(90)
            b_.setMinimumHeight(30)
        row.addWidget(cancel)
        row.addWidget(ok)
        self.lay.addLayout(row)
        self.setMinimumWidth(480)

    @property
    def separately(self) -> bool:
        return self.separate_radio is not None and self.separate_radio.isChecked()

    def options(self) -> dict:
        w, h = self.res.currentData()
        return {"width": w, "height": h, "crf": self.quality.currentData()}


class _Bridge(QObject):
    progress = Signal(float)
    done = Signal(object)
    failed = Signal(str)


class ProgressDialog(_Dialog):
    def __init__(self, title: str, parent=None):
        super().__init__(title, parent)
        a = self._appearance
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(True)
        # A QProgressBar starts at value -1, which draws no text at all; show
        # what's happening until the first frames are done.
        self.bar.setValue(0)
        self.bar.setFormat("Preparing…")
        self.bar.setStyleSheet(
            f"QProgressBar {{ background-color: {a.afterglow_color_card_background}; color: {a.card_text_color};"
            f" border: 1px solid {a.afterglow_color_accent}; border-radius: 8px; text-align: center; height: 22px; }}"
            f"QProgressBar::chunk {{ background-color: {a.afterglow_color_accent}; border-radius: 7px; }}")
        self.lay.addWidget(self.bar)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = CustomButton("Cancel")
        row.addWidget(self.cancel_btn)
        self.lay.addLayout(row)
        self.setMinimumWidth(420)

    def set_fraction(self, f: float) -> None:
        v = int(f * 1000)
        if v > 0 and self.bar.format() != "%p%":
            self.bar.setFormat("%p%")
        self.bar.setValue(v)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel_btn.click()
            return
        super().keyPressEvent(event)


class AdvancedEditorPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        appearance = config_module.load_readonly().appearance
        self._appearance = appearance
        self._theme = Theme(appearance)
        self.current_video_id: "int | None" = None
        self._video_path: "str | None" = None
        self._video_mtime = None
        self._neighbor_provider = None
        self.setFocusPolicy(Qt.ClickFocus)   # clicks on empty areas keep the shortcuts working
        self.ctl = EditorController(self)
        self.visuals = TimelineVisuals(self)
        text_c = appearance.card_text_color

        outer = QVBoxLayout(self)
        m = BORDER_WIDTH + 6
        outer.setContentsMargins(m, m, m, m)
        outer.setSpacing(8)

        # ---- header
        header = QHBoxLayout()
        self.import_btn = CustomButton("Import")
        self.import_btn.setToolTip("Edit any video/audio file. Saving writes <name>-edited next to it; "
                                   "the original is never changed.")
        self.import_btn.clicked.connect(self.import_file)
        header.addWidget(self.import_btn)
        self.name_edit = CustomLineEdit()
        self.name_edit.setPlaceholderText("No clip open")
        self.name_edit.editingFinished.connect(self._on_name_edited)
        header.addWidget(self.name_edit, stretch=1)
        self.unsaved_label = QLabel("")
        self.unsaved_label.setStyleSheet("QLabel { color: #ffb347; font-weight: bold; }")
        header.addWidget(self.unsaved_label)
        header.addSpacing(12)
        self.discard_btn = CustomButton("Discard Changes")
        self.discard_btn.setToolTip("Throw away every change since the last Save Edits / Export")
        self.discard_btn.setMinimumWidth(130)
        self.discard_btn.clicked.connect(self.discard_changes)
        header.addWidget(self.discard_btn)
        header.addSpacing(6)
        self.save_edits_btn = CustomButton("Save Edits")
        self.save_edits_btn.setToolTip("Save the project as it is (Ctrl+S) -- the video itself isn't changed "
                                       "until you Export")
        self.save_edits_btn.setMinimumWidth(100)
        self.save_edits_btn.clicked.connect(self.save_edits)
        header.addWidget(self.save_edits_btn)
        header.addSpacing(6)
        self.save_btn = CustomButton("Export")
        self.save_btn.setToolTip("Render the edit into the video (Ctrl+E)")
        self.save_btn.setMinimumWidth(90)
        self.save_btn.clicked.connect(self.save)
        header.addWidget(self.save_btn)
        header.addSpacing(6)
        # Filters (top right): the clip's library filters/tags, the same
        # panel the previewer's Filters button opens.
        self.filters_btn = CustomButton("Filters")
        self.filters_btn.setToolTip("Change this clip's filters")
        self.filters_btn.setMinimumWidth(90)
        self.filters_btn.clicked.connect(self.toggle_filters_panel)
        header.addWidget(self.filters_btn)
        self._filters_panel = None
        outer.addLayout(header)

        # ---- panels
        self.browser = BrowserPanel(self.ctl)
        self.preview = PreviewPanel(self.ctl)
        self.properties = PropertiesPanel(self.ctl)
        top = QSplitter(Qt.Horizontal)
        top.setChildrenCollapsible(False)
        top.addWidget(_Panel(self.browser))
        top.addWidget(_Panel(self.preview))
        top.addWidget(_Panel(self.properties))
        top.setStretchFactor(0, 3)
        top.setStretchFactor(1, 6)
        top.setStretchFactor(2, 3)
        top.setSizes([330, 900, 340])
        self.browser.setMinimumWidth(220)
        self.properties.setMinimumWidth(310)

        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(6)
        tools = QHBoxLayout()
        tools.setSpacing(6)

        def tool(name, tip, slot, checkable=False):
            b = CustomButton()
            b.set_circular(34)
            b.set_icon_pixmap(icons.icon(name, text_c))
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.clicked.connect(slot)
            tools.addWidget(b)
            return b
        self.undo_btn = tool("undo", "Undo (Ctrl+Z)", self.ctl.undo)
        self.redo_btn = tool("redo", "Redo (Ctrl+Shift+Z)", self.ctl.redo)
        tools.addSpacing(10)
        tool("split", "Split at playhead (S)", self.ctl.split)
        tool("combine", "Combine touching segments (C)", self.ctl.combine)
        tool("trash", "Delete (Del) -- Shift+Del ripple-deletes", self.ctl.delete)
        tools.addSpacing(10)
        self.snap_btn = tool("magnet", "Snapping (N) -- hold Alt while dragging to bypass", self._toggle_snap, True)
        self.snap_btn.setChecked(True)
        self._style_snap()
        tools.addSpacing(10)
        self.cleanup_btn = CustomButton("Clean Up")
        self.cleanup_btn.setToolTip("Pull everything as close to the middle tracks as it goes and remove "
                                    "empty tracks (times and layering stay the same)")
        self.cleanup_btn.setMinimumHeight(30)
        self.cleanup_btn.setMinimumWidth(90)
        self.cleanup_btn.clicked.connect(self.clean_up)
        tools.addWidget(self.cleanup_btn)
        tools.addStretch(1)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"QLabel {{ color: {text_c}; }}")
        tools.addWidget(self.status_label)
        tools.addStretch(1)
        self.timeline = TimelinePanel(self.ctl, self.visuals)
        tool("zoom_out", "Zoom out (Ctrl+wheel, Ctrl+-)", lambda: self.timeline.view.zoom_by(-1))
        tool("zoom_fit", "Zoom to fit (Ctrl+0)", self.timeline.view.zoom_to_fit)
        tool("zoom_in", "Zoom in (Ctrl+wheel, Ctrl+=)", lambda: self.timeline.view.zoom_by(1))
        bl.addLayout(tools)
        bl.addWidget(self.timeline, stretch=1)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.addWidget(top)
        split.addWidget(_Panel(bottom))
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 4)
        split.setSizes([520, 400])
        outer.addWidget(split, stretch=1)
        self._splitters = (top, split)
        qss = (f"QSplitter::handle {{ background: transparent; }}"
               f"QSplitter::handle:hover {{ background: {appearance.afterglow_color_accent}; border-radius: 3px; }}")
        for s in self._splitters:
            s.setHandleWidth(8)
            s.setStyleSheet(qss)

        self.ctl.state_changed.connect(self._update_state)
        self.ctl.error.connect(self._show_error)
        self.ctl.playhead_changed.connect(self._follow_playhead)
        self.timeline.view.open_properties.connect(lambda: self.properties.setFocus())
        self._status_timer = QTimer(self)
        self._status_timer.setSingleShot(True)
        self._status_timer.timeout.connect(lambda: self.status_label.setText(""))
        self._install_shortcuts()
        self._update_state()

    # ================================================================ API used by MainWindow
    def load_video(self, video_id: int) -> None:
        try:
            video = library.get_video(video_id)
        except Exception as e:
            self._show_error(str(e))
            return
        if not Path(video.path).exists():
            self._show_error("The clip's file is missing.")
            return
        self.preview.stop()
        try:
            project = nle_save.open_for_library_video(video)
        except Exception as e:
            show_message(self, "Can't Open Clip", str(e))
            return
        self.current_video_id = video_id
        self._video_path = str(video.path)
        self._video_mtime = self._file_mtime(self._video_path)
        self.ctl.set_project(project, "library")
        self.name_edit.setText(video.title)
        self.name_edit.setEnabled(True)
        self.timeline.view.request_fit()
        self.preview.warm_up_audio()
        self._flash(f"Opened {video.title}")

    def set_neighbor_provider(self, provider) -> None:
        # Prev/Next moved to the previewer; kept so MainWindow's wiring stays valid.
        self._neighbor_provider = provider

    def apply_scale(self, factor: float) -> None:
        pass

    def shutdown(self) -> None:
        self.ctl.flush_edits()          # a still-open grouped edit would block the autosave
        self.ctl.autosave_now()
        self.preview.shutdown()
        self.visuals.shutdown()

    # ================================================================ header actions
    def import_file(self) -> None:
        path, _ = get_open_file_name(
            self, "Import a file to edit", str(Path.home()),
            "Media (*.mp4 *.mkv *.mov *.webm *.avi *.flv *.m4v *.ts *.mp3 *.wav *.flac *.ogg *.m4a);;All files (*)")
        if path:
            self.open_import(path)

    def open_import(self, path: str) -> None:
        self.preview.stop()
        try:
            project = nle_save.open_for_import(path)
        except Exception as e:
            show_message(self, "Can't Import", str(e))
            return
        self.current_video_id = None
        self._video_path = None
        self.ctl.set_project(project, "import", import_source=path)
        self.name_edit.setText(Path(project.output_path).stem)
        self.name_edit.setEnabled(True)
        self.timeline.view.request_fit()
        self.preview.warm_up_audio()
        self._flash(f"Imported {os.path.basename(path)} -- saves as {os.path.basename(project.output_path)}")

    def _on_name_edited(self) -> None:
        name = self.name_edit.text().strip()
        if not self.ctl.has_project:
            return
        if self.ctl.mode == "library" and self.current_video_id is not None:
            video = library.get_video(self.current_video_id)
            if not name:
                self.name_edit.setText(video.title)
                return
            if name != video.title:
                old = str(video.path)
                self.ctl.autosave_now()
                video = library.rename_video(self.current_video_id, title=name)
                self._sync_video_path(old, str(video.path))
        elif self.ctl.mode == "import":
            p = self.ctl.project
            if not name:
                self.name_edit.setText(Path(p.output_path).stem)
                return
            out = Path(p.output_path)
            p.output_path = str(out.with_name(name + out.suffix))

    def _sync_video_path(self, old: str, new: str) -> None:
        if old != new:
            self.ctl.remap_sources({old: new})
        self._video_path = new

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.ctl.has_project:
            self.preview.warm_up_audio()
        # The clip may have been renamed (its file too) or edited elsewhere
        # (previewer quick trim, Undo Edits) since it was opened here.
        if self.ctl.mode == "library" and self.current_video_id is not None:
            try:
                video = library.get_video(self.current_video_id)
            except Exception:
                self.ctl.clear()
                self.current_video_id = None
                self.name_edit.setText("")
                return
            if self._video_path and str(video.path) != self._video_path:
                self._sync_video_path(self._video_path, str(video.path))
            if not self.name_edit.hasFocus():
                self.name_edit.setText(video.title)
            if self._video_mtime is not None and self._file_mtime(str(video.path)) != self._video_mtime:
                # The file changed outside the editor (a quick trim in the
                # previewer, Undo Edits...). Those discard the stored edit,
                # so reopen to show what the clip is now.
                self.load_video(video.id)
                self._flash("The clip was changed outside the editor -- reopened it")

    @staticmethod
    def _file_mtime(path: str):
        try:
            return os.stat(path).st_mtime_ns
        except OSError:
            return None

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.close_filters_panel()
        self.preview.stop()
        self.preview.release_audio()
        self.ctl.flush_edits()
        self.ctl.autosave_now()

    # ================================================================ discard
    def discard_changes(self, confirm: bool = True) -> None:
        if not self.ctl.has_project or not self.ctl.unsaved:
            return
        if confirm and not ask_confirm(
                self, "Discard Changes?",
                "Every change since the last Save will be lost (this can't be undone).", "Discard"):
            return
        self.preview.stop()
        mode = self.ctl.mode
        playhead = self.ctl.playhead

        def fresh():
            if mode == "library" and self.current_video_id is not None:
                video = library.get_video(self.current_video_id)
                return nle_save.new_project_for_file(str(video.path), library_video_id=video.id,
                                                     output_path=str(video.path))
            src = self.ctl.import_source
            out = self.ctl.project.output_path
            return nle_save.new_project_for_file(src, output_path=out)
        try:
            self.ctl.discard_changes(fresh)
        except Exception as e:
            show_message(self, "Can't Discard", str(e))
            return
        self.ctl.set_playhead(min(playhead, self.ctl.project.duration))
        self.timeline.view.request_fit()
        self._flash("Changes discarded")

    # ================================================================ save edits
    def save_edits(self) -> None:
        if not self.ctl.has_project:
            return
        if self.ctl.save_edits():
            self._flash("Edits saved -- Export puts them into the video")

    # ================================================================ export
    def save(self) -> None:
        """Export: render the edit into the video."""
        if not self.ctl.has_project:
            return
        p = self.ctl.project
        if p.duration <= 0:
            show_message(self, "Nothing to Export", "The timeline is empty.")
            return
        self.preview.stop()
        if self.ctl.mode == "library":
            target = ("Renders the timeline over this clip. The original is kept in Edit Backups, so "
                      "Undo Edits (Library/previewer) restores it. The edit stays editable here.")
        else:
            target = f"Writes {os.path.basename(p.output_path)} next to the original (which is not changed)."
        dlg = SaveDialog(p, target, self, library_clip=self.ctl.mode == "library")
        if dlg.exec() != QDialog.Accepted:
            return
        opts = dlg.options()
        separately = dlg.separately
        work = Project.from_dict(copy.deepcopy(p.to_dict()))
        cancel = threading.Event()
        bridge = _Bridge()
        prog = ProgressDialog("Exporting…", self)
        prog.cancel_btn.clicked.connect(cancel.set)
        bridge.progress.connect(prog.set_fraction)
        result = {}

        def finished(value):
            result["ok"] = value
            prog.accept()

        def failed(msg):
            result["err"] = msg
            prog.reject()
        bridge.done.connect(finished)
        bridge.failed.connect(failed)
        mode = self.ctl.mode
        source = self.ctl.import_source

        def run():
            try:
                if mode == "library" and separately:
                    out = nle_save.save_library_separately(work, progress=bridge.progress.emit, cancel=cancel, **opts)
                elif mode == "library":
                    out = nle_save.save_library_clip(work, progress=bridge.progress.emit, cancel=cancel, **opts)
                else:
                    out = nle_save.save_import(work, source, progress=bridge.progress.emit, cancel=cancel, **opts)
                bridge.done.emit(out)
            except Exception as e:           # includes ExportCancelled
                from ...nle.render import ExportCancelled
                bridge.failed.emit("cancelled" if isinstance(e, ExportCancelled) else str(e))
        th = threading.Thread(target=run, daemon=True)
        th.start()
        prog.exec()
        th.join(timeout=30)
        if "err" in result:
            if result["err"] != "cancelled":
                show_message(self, "Export Failed", result["err"])
            else:
                self._flash("Export cancelled")
            return
        # The saved copy's sources may have been re-pointed at stable
        # backups (library clips). Carry that over to the live project and
        # its undo history, so later re-saves render from the originals.
        mapping = {}
        live_parts = [pt for s in p.all_segments() for pt in s.parts]
        saved_parts = [pt for s in work.all_segments() for pt in s.parts]
        for a, b in zip(live_parts, saved_parts):
            if a.source != b.source and a.source:
                mapping[a.source] = b.source
        self.ctl.remap_sources(mapping)
        self.ctl.mark_rendered()
        if mode == "library" and self._video_path:
            self._video_mtime = self._file_mtime(self._video_path)
        if mode == "library" and separately:
            self._flash(f"Exported as a new clip: {getattr(result.get('ok'), 'title', '')}")
        elif mode == "library":
            self._flash("Exported -- the clip now has your edit (Undo Edits restores the original)")
        else:
            self._flash(f"Exported {os.path.basename(str(result.get('ok')))}")

    # ================================================================ misc
    def clean_up(self) -> None:
        if not self.ctl.has_project:
            return
        from ...nle import ops
        before = len(self.ctl.project.tracks)
        self.ctl.perform("Clean up", ops.clean_up)
        after = len(self.ctl.project.tracks)
        self._flash(f"Cleaned up -- {before} tracks → {after}" if after != before else "Cleaned up")

    def _toggle_snap(self) -> None:
        self.ctl.snapping = not self.ctl.snapping
        self.snap_btn.setChecked(self.ctl.snapping)
        self._style_snap()
        self._flash("Snapping on" if self.ctl.snapping else "Snapping off")

    def _style_snap(self) -> None:
        # Lit (highlight color) while snapping is on, plain when off.
        self.snap_btn.set_fill_color(self._theme.turquoise() if self.ctl.snapping else None)

    # ================================================================ filters
    def toggle_filters_panel(self) -> None:
        if self._filters_panel is not None:
            self.close_filters_panel()
            return
        if self.ctl.mode != "library" or self.current_video_id is None:
            return
        from ..preview_filters_panel import PreviewFiltersPanel
        panel = PreviewFiltersPanel(self.current_video_id, parent=self)
        panel.close_requested.connect(self.close_filters_panel)
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

    def close_filters_panel(self) -> None:
        if self._filters_panel is not None:
            self._filters_panel.hide()
            self._filters_panel.setParent(None)
            self._filters_panel.deleteLater()
            self._filters_panel = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_filters_panel()

    def _update_state(self) -> None:
        has = self.ctl.has_project
        library_clip = has and self.ctl.mode == "library" and self.current_video_id is not None
        self.filters_btn.setEnabled(library_clip)
        self.filters_btn.setToolTip("Change this clip's filters" if library_clip
                                    else "Filters are for Library clips (not imported files)")
        if not library_clip or (self._filters_panel is not None
                                and self._filters_panel._video_id != self.current_video_id):
            self.close_filters_panel()
        h = self.ctl.history
        self.undo_btn.setEnabled(bool(h and h.can_undo()))
        self.redo_btn.setEnabled(bool(h and h.can_redo()))
        self.undo_btn.setToolTip(f"Undo {h.undo_label()} (Ctrl+Z)" if h and h.can_undo() else "Undo (Ctrl+Z)")
        self.redo_btn.setToolTip(f"Redo {h.redo_label()} (Ctrl+Shift+Z)" if h and h.can_redo() else "Redo")
        self.save_btn.setEnabled(has)
        self.discard_btn.setEnabled(has and self.ctl.unsaved)
        self.save_edits_btn.setEnabled(has and self.ctl.unsaved)
        if self.ctl.unsaved:
            self.unsaved_label.setStyleSheet("QLabel { color: #ffb347; font-weight: bold; }")
            self.unsaved_label.setText("● Unsaved changes")
        elif has and self.ctl.export_pending:
            self.unsaved_label.setStyleSheet("QLabel { color: #8fc7ff; font-weight: bold; }")
            self.unsaved_label.setText("● Saved, not exported")
        else:
            self.unsaved_label.setText("")
        if not has:
            self.name_edit.setText("")
            self.name_edit.setEnabled(False)

    def _show_error(self, msg: str) -> None:
        self._flash(msg, 4000)

    def _flash(self, msg: str, ms: int = 2500) -> None:
        self.status_label.setText(msg)
        self._status_timer.start(ms)

    def _follow_playhead(self, _t: float) -> None:
        if self.preview.playing:
            self.timeline.view.ensure_playhead_visible()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        paint_page_outline(self, self._theme.app_background())

    def _install_shortcuts(self) -> None:
        ctl = self.ctl
        view = self.timeline.view

        def sc(keys, fn):
            for k in keys if isinstance(keys, (list, tuple)) else [keys]:
                s = QShortcut(QKeySequence(k), self)
                s.setContext(Qt.WidgetWithChildrenShortcut)
                s.activated.connect(fn)

        def need(fn):
            return lambda: fn() if ctl.has_project else None
        sc("Space", need(self.preview.toggle_play))
        sc("S", need(ctl.split))
        sc("C", need(ctl.combine))
        sc("L", need(lambda: ctl.toggle("locked")))
        sc("M", need(lambda: ctl.toggle("muted")))
        sc("V", need(lambda: ctl.toggle("visible")))
        sc("H", need(ctl.toggle_hidden_layers))
        sc("N", self._toggle_snap)
        sc("Ctrl+C", need(ctl.copy))
        sc("Ctrl+X", need(ctl.cut))
        sc("Ctrl+V", need(ctl.paste))
        sc("Ctrl+D", need(ctl.duplicate))
        sc(["Delete", "Backspace"], need(ctl.delete))
        sc(["Shift+Delete", "Shift+Backspace"], need(ctl.ripple_delete))
        sc("Ctrl+Z", need(ctl.undo))
        sc(["Ctrl+Shift+Z", "Ctrl+Y"], need(ctl.redo))
        sc("Ctrl+A", need(ctl.select_all))
        sc("Escape", need(lambda: ctl.set_selection([])))
        sc(",", need(lambda: ctl.step_playhead(frames=-1)))
        sc(".", need(lambda: ctl.step_playhead(frames=1)))
        sc(["Left"], need(lambda: ctl.step_playhead(frames=-1)))
        sc(["Right"], need(lambda: ctl.step_playhead(frames=1)))
        sc(["Shift+,", "<", "Shift+Left"], need(lambda: ctl.step_playhead(seconds=-1)))
        sc(["Shift+.", ">", "Shift+Right"], need(lambda: ctl.step_playhead(seconds=1)))
        sc("Home", need(lambda: ctl.set_playhead(0.0)))
        sc("End", need(lambda: ctl.set_playhead(ctl.project.duration)))
        sc("Ctrl+S", need(self.save_edits))
        sc("Ctrl+E", need(self.save))
        sc("Ctrl+0", need(view.zoom_to_fit))
        sc(["Ctrl+=", "Ctrl++"], need(lambda: view.zoom_by(1)))
        sc("Ctrl+-", need(lambda: view.zoom_by(-1)))
        sc("Ctrl+I", self.import_file)

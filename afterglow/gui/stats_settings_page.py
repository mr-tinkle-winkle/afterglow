"""
Settings > Stats: a snapshot of the library laid out as tiles, bars and
records -- and every one of them is a button:

  * a COUNT (unedited clips, clips with a filter, clips of one clip type,
    this week's clips...) opens the Library showing exactly those clips;
  * a FILTER's bar opens the Library filtered by that filter;
  * a RECORD (longest, shortest, largest, newest, oldest clip) opens that
    clip in the previewer.

The page itself only says what was clicked (filter_requested /
open_video_requested); MainWindow.show_library_filtered() and
MainWindow.open_video_by_id() do the work -- found through window(), since
the Settings page (and this tab with it) is rebuilt on Import/Reset.

Recomputed when its Refresh button is pressed and when the tab is shown
after the library changed (cheap check: the DB file's modification time) --
not on every card change.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QRectF, QSize
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QFontMetrics
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QGridLayout,
                               QSizePolicy)
from .smooth_scroll_area import SmoothScrollArea

from .. import library
from .custom_button import CustomButton
from .theme import Theme, contrast_text


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_long_duration(seconds: float) -> str:
    total = round(seconds or 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"


def _format_size(n: int | None) -> str:
    if not n:
        return "--"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return "--"


def _format_date(iso: str | None) -> str:
    if not iso:
        return "--"
    from datetime import datetime, timezone
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return str(iso)[:10]
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone().strftime("%b %-d, %Y")


def _pct(count: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{round(count / total * 100)}%"


class _Clickable(QWidget):
    """A rounded panel that acts as a button when it has something to do:
    hover highlight, pointing-hand cursor, click / Enter / Space. Painted
    here (no stylesheet: one on a container would cascade into every child)."""
    activated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = False
        self._pressed = False
        self._enabled_action = False
        self.setAttribute(Qt.WA_Hover, True)

    def set_action(self, tooltip: str) -> None:
        self._enabled_action = True
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.TabFocus)
        self.setToolTip(tooltip)

    @property
    def clickable(self) -> bool:
        return self._enabled_action

    def _colors(self) -> tuple[QColor, QColor, QColor]:
        """(background, text, accent)"""
        theme = Theme()
        if theme.afterglow_enabled:
            bg, accent = theme.card_background(), theme.turquoise()
        else:
            pal = self.palette()
            bg, accent = pal.color(QPalette.AlternateBase), pal.color(QPalette.Highlight)
        return bg, contrast_text(bg), accent

    def _radius(self) -> float:
        theme = Theme()
        return float(min(theme.corner_radius, 14)) if theme.rounded_corners_enabled else 0.0

    def paintEvent(self, event) -> None:
        bg, _text, accent = self._colors()
        if self._enabled_action and self._pressed:
            bg = bg.darker(112)
        elif self._enabled_action and self._hover:
            bg = bg.lighter(118)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        r = self._radius()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), r, r)
        p.fillPath(path, bg)
        if self._enabled_action and (self._hover or self.hasFocus()):
            pen = p.pen()
            pen.setColor(accent)
            pen.setWidthF(1.5)
            p.setPen(pen)
            p.drawPath(path)
        p.end()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = self._pressed = False
        self.update()

    def mousePressEvent(self, event) -> None:
        if self._enabled_action and event.button() == Qt.LeftButton:
            self._pressed = True
            self.update()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        was = self._pressed
        self._pressed = False
        self.update()
        if was and self._enabled_action and event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.activated.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._enabled_action and event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.activated.emit()
        else:
            super().keyPressEvent(event)

    def _label(self, text: str, size_delta: float = 0.0, bold: bool = False, dim: bool = False) -> QLabel:
        label = QLabel(text)
        label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        f = label.font()
        if size_delta:
            f.setPointSizeF(max(6.0, f.pointSizeF() + size_delta))
        f.setBold(bold)
        label.setFont(f)
        color = self._colors()[1]
        if dim:
            color.setAlpha(170)
        pal = label.palette()
        pal.setColor(QPalette.WindowText, color)
        label.setPalette(pal)
        return label


class StatTile(_Clickable):
    """One big number with its name under it (and an optional detail line)."""

    def __init__(self, value: str, name: str, detail: str = "", parent=None):
        super().__init__(parent)
        self.value_text, self.name_text, self.detail_text = value, name, detail
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(1)
        self.value_label = self._label(value, size_delta=8, bold=True)
        self.name_label = self._label(name)
        layout.addWidget(self.value_label)
        layout.addWidget(self.name_label)
        # always there (blank when there is no detail) so every tile in a row is the same height
        self.detail_label = self._label(detail or " ", size_delta=-1, dim=True)
        layout.addWidget(self.detail_label)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumWidth(130)


class BarRow(_Clickable):
    """name ......[#######      ]  12 | 34%"""

    def __init__(self, name: str, count: int, total: int, parent=None):
        super().__init__(parent)
        self.name_text, self.count, self.total = name, count, total
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(360, 30)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        _bg, text, accent = self._colors()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self.font())
        w, h = self.width(), self.height()
        name_w = min(max(110, w // 4), 220)
        right_w = fm.horizontalAdvance("0000 | 100%") + 8
        p.setPen(text)
        p.drawText(QRectF(12, 0, name_w - 12, h), Qt.AlignVCenter | Qt.AlignLeft,
                   fm.elidedText(self.name_text, Qt.ElideRight, name_w - 14))
        p.drawText(QRectF(w - right_w - 10, 0, right_w, h), Qt.AlignVCenter | Qt.AlignRight,
                   f"{self.count} | {_pct(self.count, self.total)}")
        bar = QRectF(name_w + 6, h / 2 - 4, max(10, w - name_w - right_w - 28), 8)
        track = QColor(text)
        track.setAlpha(40)
        path = QPainterPath()
        path.addRoundedRect(bar, 4, 4)
        p.fillPath(path, track)
        frac = (self.count / self.total) if self.total > 0 else 0.0
        if frac > 0:
            fill = QPainterPath()
            fill.addRoundedRect(QRectF(bar.x(), bar.y(), max(8.0, bar.width() * min(1.0, frac)), bar.height()), 4, 4)
            p.fillPath(fill, accent)
        p.end()


class RecordRow(_Clickable):
    """Longest clip      <title>                         2:31"""

    def __init__(self, name: str, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.name_text, self.title_text, self.detail_text = name, title, detail
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        name_label = self._label(name, dim=True)
        name_label.setFixedWidth(120)
        self.title_label = self._label(title, bold=True)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # long titles elide, never widen the page
        self.detail_label = self._label(detail)
        layout.addWidget(name_label)
        layout.addWidget(self.title_label, stretch=1)
        layout.addWidget(self.detail_label)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)


def _on_page_color(widget: QWidget, dim: bool = False) -> None:
    """Text colour for a label sitting directly on the page: readable on the
    Afterglow theme's own background whatever the system palette says."""
    theme = Theme()
    if not theme.afterglow_enabled:
        return
    color = contrast_text(theme.app_background())
    if dim:
        color.setAlpha(170)
    pal = widget.palette()
    for group in (QPalette.Active, QPalette.Inactive, QPalette.Disabled):
        pal.setColor(group, QPalette.WindowText, color)
    widget.setPalette(pal)


class StatsPage(QWidget):
    # {"label": str, "ids": list[int] | None, "tags": list[str] | None, "tab": "local" | "uploaded" (optional)}
    filter_requested = Signal(dict)
    open_video_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)

        header_row = QHBoxLayout()
        title = QLabel("<b>Library Stats</b>")
        header_row.addWidget(title)
        self.hint_label = QLabel("Click a stat to see those clips in the Library, or a record to open the clip.")
        _on_page_color(title)
        _on_page_color(self.hint_label, dim=True)
        header_row.addWidget(self.hint_label, stretch=1)
        self.refresh_btn = CustomButton("Refresh")
        self.refresh_btn.setProperty("_ag_no_autosave", True)
        self.refresh_btn.setToolTip("Recompute stats from the current library")
        self.refresh_btn.clicked.connect(self.refresh_stats)
        header_row.addWidget(self.refresh_btn)
        outer.addLayout(header_row)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setAlignment(Qt.AlignTop)
        self._body_layout.setSpacing(8)
        scroll.setWidget(self._body)
        outer.addWidget(scroll, stretch=1)

        self._rows: list[QWidget] = []
        self.tiles: dict[str, StatTile] = {}          # by key, for tests and keyboard users
        self.filter_rows: dict[str, BarRow] = {}
        self.type_rows: dict[str, BarRow] = {}
        self.record_rows: dict[str, RecordRow] = {}
        self._db_mtime = None
        self.filter_requested.connect(self._default_filter_handler)
        self.open_video_requested.connect(self._default_open_handler)
        self.refresh_stats()

    # ------------------------------------------------------------ actions

    def _default_filter_handler(self, request: dict) -> None:
        handler = getattr(self.window(), "show_library_filtered", None)
        if handler is not None:
            handler(request)

    def _default_open_handler(self, video_id: int) -> None:
        handler = getattr(self.window(), "open_video_by_id", None)
        if handler is not None:
            handler(video_id)

    def _db_stamp(self):
        try:
            from .. import db
            return db.DB_PATH.stat().st_mtime_ns
        except Exception:  # noqa: BLE001
            return None

    def showEvent(self, event) -> None:
        super().showEvent(event)
        stamp = self._db_stamp()
        if stamp is not None and stamp != self._db_mtime:
            self.refresh_stats()

    # ------------------------------------------------------------ building

    def _section(self, text: str) -> None:
        label = QLabel(f"<b>{text}</b>")
        label.setContentsMargins(2, 8, 0, 0)
        _on_page_color(label)
        self._body_layout.addWidget(label)
        self._rows.append(label)

    def _tile_grid(self, tiles: list, columns: int = 4) -> None:
        holder = QWidget()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(8)
        for i, tile in enumerate(tiles):
            grid.addWidget(tile, i // columns, i % columns)
        for c in range(columns):
            grid.setColumnStretch(c, 1)
        self._body_layout.addWidget(holder)
        self._rows.append(holder)

    def _tile(self, key: str, value: str, name: str, detail: str = "", label: str = "",
              ids: "list | None" = None, tab: str = "local") -> StatTile:
        tile = StatTile(value, name, detail)
        self.tiles[key] = tile
        if ids is not None and label and len(ids) > 0:
            tile.set_action("Show these clips in the Library")
            tile.activated.connect(lambda ids=list(ids), label=label: self.filter_requested.emit(
                {"label": label, "ids": ids, "tags": None, "tab": tab}))
        return tile

    def _note(self, text: str) -> None:
        label = QLabel(text)
        _on_page_color(label, dim=True)
        label.setContentsMargins(4, 0, 0, 0)
        self._body_layout.addWidget(label)
        self._rows.append(label)

    def refresh_stats(self) -> None:
        """Recompute from the DB and rebuild everything shown."""
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        self.tiles, self.filter_rows, self.type_rows, self.record_rows = {}, {}, {}, {}

        stats = library.compute_stats()
        self.stats = stats
        self._db_mtime = self._db_stamp()
        total = stats.total

        # ---- overview
        self._section("Overview")
        all_ids = stats.edited_ids + stats.unedited_ids
        self._tile_grid([
            self._tile("total", str(total), "clips", label="All clips", ids=all_ids),
            self._tile("length", _format_long_duration(stats.total_length_sec), "total length",
                       f"average {_format_duration(stats.avg_length_sec)}"),
            self._tile("size", _format_size(stats.total_size_bytes), "on disk",
                       f"{len(stats.local_ids)} local file" + ("" if len(stats.local_ids) == 1 else "s")),
            self._tile("week", str(len(stats.last_7_days_ids)), "in the last 7 days",
                       f"{len(stats.last_30_days_ids)} in the last 30 days",
                       label="Clips from the last 7 days", ids=stats.last_7_days_ids),
        ])

        # ---- state
        self._section("Editing and sharing")
        self._tile_grid([
            self._tile("unedited", str(stats.unedited), "unedited", _pct(stats.unedited, total),
                       label="Unedited clips", ids=stats.unedited_ids),
            self._tile("edited", str(stats.edited), "edited", _pct(stats.edited, total),
                       label="Edited clips", ids=stats.edited_ids),
            self._tile("favorites", str(len(stats.favorite_ids)), "favorites", _pct(len(stats.favorite_ids), total),
                       label="Favorite clips", ids=stats.favorite_ids),
            self._tile("uploaded", str(len(stats.uploaded_ids)), "uploaded", _pct(len(stats.uploaded_ids), total),
                       label="Uploaded clips", ids=stats.uploaded_ids, tab="uploaded"),
        ])

        # ---- filters
        self._section("Filters")
        self._tile_grid([
            self._tile("with_filter", str(stats.with_filter), "with a filter", _pct(stats.with_filter, total),
                       label="Clips with a filter", ids=stats.with_filter_ids),
            self._tile("no_filter", str(len(stats.no_filter_ids)), "without a filter",
                       _pct(len(stats.no_filter_ids), total),
                       label="Clips without a filter", ids=stats.no_filter_ids),
        ], columns=4)
        ranked = sorted(stats.per_filter.items(), key=lambda kv: (-kv[1], kv[0].lower()))
        for tag, count in ranked:
            row = BarRow(tag, count, total)
            if count > 0:
                row.set_action(f"Show the Library filtered by “{tag}”")
                row.activated.connect(lambda tag=tag: self.filter_requested.emit(
                    {"label": f"Filter: {tag}", "ids": None, "tags": [tag]}))
            self.filter_rows[tag] = row
            self._body_layout.addWidget(row)
            self._rows.append(row)
        if not ranked:
            self._note("No filters yet. Add one from a clip's Filters panel or Settings > Filters.")

        # ---- clip types
        if stats.per_clip_type:
            self._section("Clip types")
            for name, ids in stats.per_clip_type.items():
                row = BarRow(name, len(ids), total)
                row.set_action(f"Show the “{name}” clips in the Library")
                row.activated.connect(lambda name=name, ids=list(ids): self.filter_requested.emit(
                    {"label": f"Clip type: {name}", "ids": ids, "tags": None}))
                self.type_rows[name] = row
                self._body_layout.addWidget(row)
                self._rows.append(row)

        # ---- records
        self._section("Records")
        records = [
            ("longest", "Longest clip", stats.longest_id, stats.longest_title, _format_duration(stats.longest_length_sec)),
            ("shortest", "Shortest clip", stats.shortest_id, stats.shortest_title, _format_duration(stats.shortest_length_sec)),
            ("largest", "Largest file", stats.largest_id, stats.largest_title, _format_size(stats.largest_size_bytes)),
            ("newest", "Newest clip", stats.newest_id, stats.newest_title, _format_date(stats.newest_created)),
            ("oldest", "Oldest clip", stats.oldest_id, stats.oldest_title, _format_date(stats.oldest_created)),
        ]
        for key, name, video_id, title, detail in records:
            row = RecordRow(name, title or "--", detail if video_id is not None else "")
            if video_id is not None:
                row.set_action("Open this clip")
                row.activated.connect(lambda video_id=video_id: self.open_video_requested.emit(video_id))
            self.record_rows[key] = row
            self._body_layout.addWidget(row)
            self._rows.append(row)

"""
Settings > Stats: a manually-refreshed snapshot of the library (counts,
per-filter breakdown, edited/unedited split, and length extremes). Read-
only -- no save() hook, unlike the other Settings tabs.

Deliberately NOT auto-refreshed on every video/tag change (unlike, say,
FiltersSettingsPage's refresh_dynamic_lists(), which SettingsPage calls
whenever the page becomes visible) -- per Max's own instruction, this
recomputes only when its own Refresh button is pressed, since a full
library scan (every video's tags, every video's duration) on every
"Settings became visible" would be needless work for a tab most
sessions never open at all.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame
from .smooth_scroll_area import SmoothScrollArea

from .. import library
from .custom_button import CustomButton


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--"
    total = round(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _pct(count: int, total: int) -> str:
    if total <= 0:
        return "0%"
    return f"{round(count / total * 100)}%"


class StatsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)

        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("<b>Library Stats</b>"), stretch=1)
        self.refresh_btn = CustomButton("Refresh")
        self.refresh_btn.setToolTip("Recompute stats from the current library")
        self.refresh_btn.clicked.connect(self.refresh_stats)
        header_row.addWidget(self.refresh_btn)
        outer.addLayout(header_row)

        scroll = SmoothScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        self._rows: list[QWidget] = []
        self.refresh_stats()

    def _add_row(self, text: str, indent: int = 0) -> QLabel:
        label = QLabel(text)
        if indent:
            wrapper = QFrame()
            wrapper_layout = QHBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(indent, 0, 0, 0)
            wrapper_layout.addWidget(label)
            self._body_layout.addWidget(wrapper)
            self._rows.append(wrapper)
        else:
            self._body_layout.addWidget(label)
            self._rows.append(label)
        return label

    def refresh_stats(self) -> None:
        """Recompute from the DB and rebuild the displayed rows. This is
        the ONLY place compute_stats() gets called -- see this module's
        docstring for why that's deliberate."""
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []

        stats = library.compute_stats()
        total = stats.total

        self._add_row(f"<b>{total}</b> videos total")
        self._add_row(
            f"<b>{stats.with_filter}</b> videos with a filter | {_pct(stats.with_filter, total)}"
        )
        for tag, count in stats.per_filter.items():
            self._add_row(f"<b>{count}</b> {tag} clips | {_pct(count, total)}", indent=24)
        self._add_row(
            f"<b>{stats.unedited}</b> unedited videos | {_pct(stats.unedited, total)}"
        )
        self._add_row(
            f"<b>{stats.edited}</b> edited videos | {_pct(stats.edited, total)}"
        )
        # Percentages don't apply to these three -- they're durations,
        # not subsets of the total video count, so there's nothing for
        # a percent to be "of" (see HANDOFF.md for this call).
        self._add_row(f"Average video length: <b>{_format_duration(stats.avg_length_sec)}</b>")
        longest = f"{stats.longest_title} ({_format_duration(stats.longest_length_sec)})" if stats.longest_title else "--"
        shortest = f"{stats.shortest_title} ({_format_duration(stats.shortest_length_sec)})" if stats.shortest_title else "--"
        self._add_row(f"Longest video: <b>{longest}</b>")
        self._add_row(f"Shortest video: <b>{shortest}</b>")

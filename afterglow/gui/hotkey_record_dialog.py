"""
Modal dialog: "Press a key combo..." -> records it via the same evdev
listener the daemon uses (through RecorderAdapter) -> returns the combo
string. Runs the evdev reading on a background QThread so the Qt event
loop / UI never blocks waiting on device reads.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QPushButton, QMessageBox

from ..hotkeys import EvdevHotkeyListener, RecorderAdapter


class _RecordThread(QThread):
    recorded = Signal(str)
    failed = Signal(str)

    def run(self) -> None:
        adapter = RecorderAdapter(on_recorded=self.recorded.emit)
        listener = EvdevHotkeyListener(adapter)
        try:
            listener.start()
        except Exception as e:
            self.failed.emit(str(e))
            return
        # Block this thread alive until a combo is recorded or we're told
        # to stop; the listener's own reader threads do the actual work.
        self._listener = listener
        while not self.isInterruptionRequested():
            self.msleep(100)
        listener.stop()

    def stop(self) -> None:
        self.requestInterruption()


class HotkeyRecordDialog(QDialog):
    def __init__(self, parent=None, current_combo: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("Record Hotkey")
        self.setModal(True)
        self.result_combo: str | None = None

        layout = QVBoxLayout(self)
        subtitle = f"Current: {current_combo}" if current_combo else "No hotkey set yet"
        self.status_label = QLabel(
            f"Press the key combo you want to use, then release it.\n{subtitle}"
        )
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        layout.addWidget(cancel_btn)

        self._thread = _RecordThread()
        self._thread.recorded.connect(self._on_recorded)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _on_recorded(self, combo: str) -> None:
        self.result_combo = combo
        self._thread.stop()
        self.accept()

    def _on_failed(self, error: str) -> None:
        self._thread.stop()
        QMessageBox.critical(
            self, "Hotkey Recording Failed",
            f"{error}\n\nThis usually means the app doesn't have permission "
            f"to read /dev/input devices. On NixOS, the user running this "
            f"needs to be in the 'input' group.",
        )
        self.reject()

    def closeEvent(self, event) -> None:
        self._thread.stop()
        self._thread.wait(500)
        super().closeEvent(event)

    def reject(self) -> None:
        self._thread.stop()
        super().reject()

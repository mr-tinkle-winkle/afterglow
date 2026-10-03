"""
Settings > YouTube: the account (sign in / out), how uploads are filled in (default visibility,
the description template with a live preview, stop-for-review, delete-after-upload), the red
indicator circle, and the upload sounds.  The playlist is per clip type (Settings > Clipping >
Clip Options > each option's "YouTube playlist").
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from .. import config as config_module
from ..youtube import PRIVACY_CHOICES, template as template_module
from .custom_button import CustomButton
from .custom_checkbox import CustomCheckBox
from .custom_combo_box import CustomComboBox
from .custom_group_box import CustomGroupBox
from .custom_line_edit import CustomLineEdit
from .custom_spinbox import CustomSpinBox
from .custom_message_dialog import ask_confirm, show_message
from .themed_dialogs import get_color, get_open_file_name


def themed_text_edit(appearance, text: str = "", rows: int = 4) -> QPlainTextEdit:
    """A multi-line field in the app's look (type-scoped stylesheet: never cascades)."""
    edit = QPlainTextEdit(text)
    edit.setStyleSheet(
        f"QPlainTextEdit {{ background-color: {appearance.afterglow_color_card_background};"
        f" color: {appearance.card_text_color}; border: 1px solid {appearance.afterglow_color_accent};"
        f" border-radius: 6px; padding: 4px; }}")
    edit.setTabChangesFocus(True)
    fm = edit.fontMetrics()
    edit.setFixedHeight(int(fm.lineSpacing() * rows + 16))
    return edit


SAMPLE_FACTS = template_module.ClipFacts(
    title="Triple kill on B", clip_type="Valorant", created_at="2026-10-03T19:17:42+00:00",
    duration_sec=32.4, size_bytes=48_300_000, filters=["Clutch", "1v3", "Ace!"])


class YouTubeSettingsPage(QWidget):
    def __init__(self, settings: "config_module.AppSettings", parent=None):
        super().__init__(parent)
        self._settings = settings
        yt = settings.youtube
        a = settings.appearance
        self._appearance = a
        lay = QVBoxLayout(self)

        # ---- account
        acct = CustomGroupBox("Account")
        form = acct.make_layout(QFormLayout)
        self.account_label = QLabel()
        self.account_label.setWordWrap(True)
        form.addRow("YouTube:", self.account_label)
        row = QHBoxLayout()
        self.signin_btn = CustomButton("Sign in...")
        self.signin_btn.clicked.connect(self._sign_in)
        self.signout_btn = CustomButton("Sign out")
        self.signout_btn.clicked.connect(self._sign_out)
        row.addWidget(self.signin_btn)
        row.addWidget(self.signout_btn)
        row.addStretch(1)
        form.addRow("", row)
        note = QLabel("Uploads go through YouTube Studio inside afterglow (YouTube's API can only upload "
                      "private videos for apps like this one). Sign in once here; whichever channel Studio "
                      "has selected is used. If Google refuses the sign-in, the Upload dialog's "
                      "“Upload in browser instead” still works.")
        note.setWordWrap(True)
        form.addRow("", note)
        lay.addWidget(acct)
        self._refresh_account(yt.account_name)

        # ---- uploads
        up = CustomGroupBox("Uploads")
        form = up.make_layout(QFormLayout)
        self.privacy_combo = CustomComboBox()
        for key, label in PRIVACY_CHOICES:
            self.privacy_combo.addItem(label, key)
        self.privacy_combo.setCurrentIndex(max(0, self.privacy_combo.findData(yt.default_privacy)))
        form.addRow("Default visibility:", self.privacy_combo)

        self.review_check = CustomCheckBox("Stop for review before saving")
        self.review_check.setChecked(yt.stop_for_review)
        self.review_check.setToolTip("Everything is filled in automatically; with this on, Studio is shown "
                                     "before the final Save and that click is yours.")
        form.addRow("", self.review_check)
        self.delete_check = CustomCheckBox("Delete the local file after uploading")
        self.delete_check.setChecked(yt.delete_local_after_upload)
        self.delete_check.setToolTip("Only once YouTube reports the upload complete and the video plays; the "
                                     "file goes to the system trash (recoverable). Never on a failure. With "
                                     "this off, the clip shows in both Local and Uploaded.")
        form.addRow("", self.delete_check)

        self.pace_spin = CustomSpinBox()
        self.pace_spin.setRange(200, 5000)
        self.pace_spin.setSingleStep(100)
        self.pace_spin.setSuffix(" ms")
        self.pace_spin.setValue(yt.step_pause_ms)
        self.pace_spin.setToolTip("Pause between automated steps in Studio (it clicks at a human pace).")
        form.addRow("Pause between steps:", self.pace_spin)

        self.template_edit = themed_text_edit(a, yt.description_template, rows=5)
        self.template_edit.textChanged.connect(self._update_preview)
        form.addRow("Description template:", self.template_edit)
        help_text = "  ".join(f"{{{k}}} {v}" for k, v in template_module.PLACEHOLDERS)
        help_label = QLabel(help_text)
        help_label.setWordWrap(True)
        help_label.setTextFormat(Qt.PlainText)
        form.addRow("", help_label)
        tpl_row = QHBoxLayout()
        reset_tpl = CustomButton("Reset template")
        reset_tpl.clicked.connect(lambda: self.template_edit.setPlainText(template_module.DEFAULT_TEMPLATE))
        tpl_row.addWidget(reset_tpl)
        tpl_row.addStretch(1)
        form.addRow("", tpl_row)
        self.preview_label = QLabel()
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextFormat(Qt.PlainText)
        self.preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow("Preview:", self.preview_label)
        lay.addWidget(up)
        self._update_preview()

        # ---- indicator + sounds
        fb = CustomGroupBox("While Uploading")
        form = fb.make_layout(QFormLayout)
        self.circle_check = CustomCheckBox("Show the red upload circle (clip indicator)")
        self.circle_check.setChecked(yt.show_upload_circle)
        self.circle_check.setToolTip("While a clip uploads and YouTube processes it, the clip indicator shows "
                                     "a red loading circle. Click it to see the upload in Studio.")
        form.addRow("", self.circle_check)
        color_row = QHBoxLayout()
        self.circle_color_edit = CustomLineEdit(yt.upload_circle_color)
        pick = CustomButton("Pick...")
        pick.clicked.connect(self._pick_color)
        color_row.addWidget(self.circle_color_edit)
        color_row.addWidget(pick)
        form.addRow("Circle color:", color_row)
        self.done_sound_edit = self._sound_row(form, "Upload-done sound:", yt.upload_done_sound)
        self.error_sound_edit = self._sound_row(form, "Upload-error sound:", yt.upload_error_sound)
        lay.addWidget(fb)
        lay.addStretch(1)

    # ------------------------------------------------------------------ helpers

    def _sound_row(self, form: QFormLayout, label: str, value: str) -> CustomLineEdit:
        row = QHBoxLayout()
        edit = CustomLineEdit(value)
        edit.setPlaceholderText("(none)")
        browse = CustomButton("Browse...")

        def pick():
            path, _ = get_open_file_name(self, "Choose a sound", "", "Audio (*.wav *.ogg *.oga *.mp3 *.flac)")
            if path:
                edit.setText(path)
        browse.clicked.connect(pick)
        row.addWidget(edit)
        row.addWidget(browse)
        form.addRow(label, row)
        return edit

    def _pick_color(self) -> None:
        c = get_color(QColor(self.circle_color_edit.text().strip() or "#ff0033"), self, "Upload Circle Color")
        if c.isValid():
            self.circle_color_edit.setText(c.name())

    def _update_preview(self) -> None:
        text = template_module.expand(self.template_edit.toPlainText(), SAMPLE_FACTS)
        self.preview_label.setText(text or "(empty description)")

    def _refresh_account(self, name: str) -> None:
        self.account_label.setText(f"Signed in: {name}" if name else "Not signed in.")
        self.signout_btn.setEnabled(bool(name))

    def _sign_in(self) -> None:
        """The sign-in window opens in the afterglow-youtube process (all web views live there)."""
        from .upload_queue import upload_queue
        q = upload_queue()
        if not getattr(self, "_signin_hooked", False):
            q.signed_in.connect(self._on_signed_in)
            self._signin_hooked = True
        self.account_label.setText("Signing in -- finish in the YouTube window...")
        q.sign_in()

    def _on_signed_in(self, name: str) -> None:
        self._settings.youtube.account_name = name
        self._refresh_account(name)

    def _sign_out(self) -> None:
        if not ask_confirm(self, "Sign Out of YouTube", "Sign out of YouTube in afterglow? Uploads will need "
                           "a new sign-in.", "Sign Out"):
            return
        from .upload_queue import upload_queue
        if not upload_queue().sign_out():
            show_message(self, "Uploads Running", "Wait for the running uploads to finish first.")
            return
        self._settings.youtube.account_name = ""
        self._refresh_account("")

    # ------------------------------------------------------------------ save

    def validate(self) -> "str | None":
        if not QColor(self.circle_color_edit.text().strip()).isValid():
            return f"'{self.circle_color_edit.text()}' isn't a valid color for the upload circle."
        return None

    def save_into(self, settings: "config_module.AppSettings") -> None:
        yt = settings.youtube
        yt.default_privacy = self.privacy_combo.currentData() or "unlisted"
        yt.stop_for_review = self.review_check.isChecked()
        yt.delete_local_after_upload = self.delete_check.isChecked()
        yt.step_pause_ms = self.pace_spin.value()
        yt.description_template = self.template_edit.toPlainText()
        yt.show_upload_circle = self.circle_check.isChecked()
        yt.upload_circle_color = self.circle_color_edit.text().strip() or "#ff0033"
        yt.upload_done_sound = self.done_sound_edit.text().strip()
        yt.upload_error_sound = self.error_sound_edit.text().strip()
        # the sign-in is written by the sign-in window itself; never overwrite it with a stale copy
        yt.account_name = config_module.load().youtube.account_name

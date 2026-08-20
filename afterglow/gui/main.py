import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .. import db
from .main_window import MainWindow


def main() -> None:
    db.init_db()
    app = QApplication(sys.argv)

    # Look for the installed icon first (Nix package layout: share/icons/
    # hicolor/.../apps/afterglow.png, resolved via the icon theme by name),
    # falling back to the repo-relative path for `python -m afterglow.gui.main`
    # during development where no icon theme install exists.
    app.setWindowIcon(QIcon.fromTheme("afterglow"))
    if app.windowIcon().isNull():
        dev_icon = (
            Path(__file__).resolve().parent.parent.parent
            / "data" / "icons" / "hicolor" / "256x256" / "apps" / "afterglow.png"
        )
        if dev_icon.exists():
            app.setWindowIcon(QIcon(str(dev_icon)))

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

import sys

from PySide6.QtWidgets import QApplication

import db
from gui.main_window import MainWindow


def main() -> None:
    db.init_db()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

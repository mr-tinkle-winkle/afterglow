import locale
import sys
from pathlib import Path

from PySide6.QtGui import QCursor, QGuiApplication, QIcon, QSurfaceFormat
from PySide6.QtWidgets import QApplication

from .. import db
from .. import config as config_module
from .main_window import MainWindow


def main() -> None:
    db.init_db()

    # Must be set before QApplication is constructed -- Qt reads the
    # default surface format when the platform's window system
    # integration initializes, which happens as part of constructing
    # QApplication itself. Without an explicit format, Qt uses its own
    # default, which isn't guaranteed to match what mpv's OpenGL render
    # API expects to draw into via QOpenGLWidget -- this is standard
    # practice for exactly this combination (an external renderer driving
    # a QOpenGLWidget) and is an additional, more foundational mitigation
    # for the reported first-frame black screen, alongside the double-
    # load workaround in mpv_widget.py's load(). Still unverified in this
    # sandbox (no GL context at all) -- if the black screen is STILL
    # happening after both of these, the next thing worth trying is a
    # different vo/render backend (e.g. "gpu-next" instead of "libmpv").
    surface_format = QSurfaceFormat()
    surface_format.setSwapInterval(1)
    surface_format.setProfile(QSurfaceFormat.CompatibilityProfile)
    QSurfaceFormat.setDefaultFormat(surface_format)

    app = QApplication(sys.argv)

    # QApplication.setWindowIcon() only covers the title bar. The tray,
    # taskbar, and dock instead identify the app by WM_CLASS, which Qt
    # derives from the running binary's name unless told otherwise -- for
    # an app launched via a Python entry point that's the interpreter
    # (python3), not afterglow, which is why the tray showed the Python
    # icon/name, and why the icon lookup then failed and fell back to the
    # empty placeholder rather than resolving through the icon theme at
    # all. setDesktopFileName() tells Qt/the window manager which
    # installed .desktop entry (data/applications/afterglow.desktop) this
    # process corresponds to, so tray/taskbar/dock icon and name resolve
    # correctly instead of falling back to the interpreter's identity.
    app.setDesktopFileName("afterglow")

    # Defense in depth alongside the same call in mpv_widget.py: QApplication
    # construction is exactly where Qt changes the process's C library
    # locale based on the desktop environment's settings, which is what
    # broke libmpv (segfault, confirmed from a real crash report -- see
    # mpv_widget.py for the full explanation). Resetting immediately after
    # QApplication exists, in addition to right before mpv itself is
    # created, covers this regardless of exactly when in the widget
    # lifecycle Qt's locale change actually takes effect.
    locale.setlocale(locale.LC_NUMERIC, "C")

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

    # Resolve the screen actually under the cursor at launch, rather
    # than letting Qt pick one on its own (not necessarily the one in
    # use) -- fixes fullscreen/maximized opening on the wrong monitor.
    # setScreen() alone doesn't reliably relocate the window's actual
    # on-screen position ahead of a fullscreen/maximize request, so
    # move() to that screen's origin first.
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    if screen is not None:
        window.setScreen(screen)
        window.move(screen.availableGeometry().topLeft())

    startup_mode = config_module.load().appearance.startup_window_mode
    if startup_mode == "fullscreen":
        window.showFullScreen()
    elif startup_mode == "maximized":
        window.showMaximized()
    else:
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

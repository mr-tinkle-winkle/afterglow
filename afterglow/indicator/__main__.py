"""``python -m afterglow.indicator`` / ``afterglow-indicator``: the indicator helper process."""
from __future__ import annotations

import argparse
import logging
import os
import sys


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="afterglow-indicator", description="afterglow clip indicator helper")
    ap.add_argument("--socket", default=None, help="socket path (default: $XDG_RUNTIME_DIR/afterglow-indicator.sock)")
    ap.add_argument("--idle-exit", type=float, default=0.0,
                    help="exit after this many idle seconds (0 = run until told to quit; the daemon's helper never exits)")
    ap.add_argument("--log-events", default=None, help="append every event + the resulting stack state to this file (debugging)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s [%(levelname)s] indicator: %(message)s")

    # The layer-shell integration turns EVERY window of the process into a layer surface, so it
    # is switched on here, in this dedicated process only -- never in the daemon or the GUI.
    # (Must happen before the QApplication exists.)
    from . import layershell
    if os.environ.get("QT_QPA_PLATFORM", "").startswith("wayland") or (
            not os.environ.get("QT_QPA_PLATFORM") and os.environ.get("WAYLAND_DISPLAY")):
        layershell.enable_in_this_process()

    from PySide6.QtWidgets import QApplication
    from .helper import IndicatorHelper
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("afterglow-indicator")
    helper = IndicatorHelper(log_path=args.log_events, idle_exit=args.idle_exit, on_quit=app.quit)
    if not helper.listen(args.socket):
        logging.getLogger("afterglow.indicator").info("another helper already owns the socket; exiting")
        return 0
    if args.idle_exit > 0:
        from PySide6.QtCore import QTimer
        idle = QTimer()
        idle.setInterval(5000)
        idle.timeout.connect(helper.step)
        idle.start()
        app._idle_timer = idle                      # keep a reference
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

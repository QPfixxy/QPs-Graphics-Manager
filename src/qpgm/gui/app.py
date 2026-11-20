"""Start the desktop app: `qpgm gui` or `qpgm-gui`."""

from __future__ import annotations

import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from qpgm.core.models import LibraryError
from qpgm.gui import icon_path, theme


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("QP's Graphics Manager")
    app.setDesktopFileName("qpgm")
    app.setWindowIcon(QIcon(icon_path("QPGM.png")))
    theme.apply(app)

    from qpgm.gui.main_window import MainWindow
    from qpgm.gui.state import AppState

    try:
        state = AppState()
    except LibraryError as e:
        QMessageBox.critical(None, "QP's Graphics Manager",
                             f"Your library file can't be read, so nothing was changed.\n\n{e}")
        return 1
    window = MainWindow(state)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

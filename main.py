"""
main.py — MuseBridge entry point.

Creates the QApplication and shows the main window.
All application logic lives in gui/main_window.py.
"""

import sys

from PySide6.QtWidgets import QApplication
from PySide6.QtCore    import Qt

from gui.main_window import MuseBridgeWindow


# =========================
# ENTRY POINT ~ QApplication must exist before any widgets are created.
# sys.argv is passed in so Qt can handle its own command-line flags
# (e.g. -platform, -style) without us doing anything extra.
# =========================
def main():
    app = QApplication(sys.argv)

    # High DPI support, keeps the bar visualizer crisp on retina displays
    app.setAttribute(Qt.AA_UseHighDpiPixmaps)

    window = MuseBridgeWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
import logging
import sys
import traceback

from PyQt6.QtWidgets import QApplication

from gui.main_window import MotaremMainWindow

logger = logging.getLogger(__name__)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MotaremMainWindow()
    window.show()

    try:
        sys.exit(app.exec())
    except Exception as e:
        logger.exception("Application error: %s", e)
        traceback.print_exc()


if __name__ == "__main__":
    main()

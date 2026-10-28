import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="PelandukOS ISO Builder")
    parser.add_argument("--check-dependencies", action="store_true")
    options = parser.parse_args()
    if options.check_dependencies:
        from core.dependencies import check
        print(json.dumps(check(), indent=2))
        return 0
    if os.geteuid() == 0:
        print("Do not run the GUI as root. Launch as your desktop user; operations use Polkit.", file=sys.stderr)
        return 1
    try:
        from PySide6.QtWidgets import QApplication
        from gui.main_window import MainWindow
    except ImportError as error:
        print(f"GUI dependency missing: {error}. Install the Qt modules described in docs/INSTALL.md "
              "or use the development virtual environment.", file=sys.stderr)
        return 2
    app = QApplication(sys.argv)
    app.setOrganizationName("Tunnelbiz Studio Sdn Bhd")
    app.setApplicationName("PelandukOS ISO Builder")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

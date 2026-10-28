import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QObject, QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from gui.preview_widget import Console


@pytest.mark.parametrize("symbol,base,code", [
    *zip('!@#$%^&*()', '1234567890', '1234567890'),
    ("_", "-", "minus"), ("+", "=", "equal"), ("?", "/", "slash"),
    (">", ".", "dot"), ("<", ",", "comma"), (":", ";", "semicolon"),
    ('"', "'", "apostrophe"), ("{", "[", "bracket_left"),
    ("}", "]", "bracket_right"), ("|", "\\", "backslash"),
    ("~", "`", "grave_accent"), ("A", "A", "a"),
])
@pytest.mark.parametrize("release_shift_first", [False, True])
def test_shift_combinations_reach_vm(symbol, base, code, release_shift_first):
    class Manager(QObject):
        frame = Signal(str)

        def __init__(self):
            super().__init__()
            self.events = []

        def command(self, name, arguments):
            assert name == "input-send-event"
            self.events.extend(arguments["events"])

    app = QApplication.instance() or QApplication([])
    manager = Manager()
    console = Console(manager)

    def send(key, down, modifiers=Qt.NoModifier):
        event = QKeyEvent(QEvent.KeyPress if down else QEvent.KeyRelease, key, modifiers)
        QApplication.sendEvent(console, event)

    send(Qt.Key_Shift, True)
    send(ord(symbol), True, Qt.ShiftModifier)
    if release_shift_first:
        send(Qt.Key_Shift, False)
        send(ord(base), False)
        expected = [("shift", True), (code, True), ("shift", False), (code, False)]
    else:
        send(ord(symbol), False, Qt.ShiftModifier)
        send(Qt.Key_Shift, False)
        expected = [("shift", True), (code, True), (code, False), ("shift", False)]
    assert [(e["data"]["key"]["data"], e["data"]["down"]) for e in manager.events] == expected
    console.close()

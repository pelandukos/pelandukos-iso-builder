from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel


class Console(QLabel):
    """QMP framebuffer with basic keyboard and absolute USB tablet input."""
    def __init__(self, manager):
        super().__init__("ISO preview")
        self.manager = manager
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(480, 300)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setStyleSheet("background:#101820;color:#d7e7ec;border-radius:6px;")
        self.image = QPixmap()
        self.pending_motion = None
        self.motion_timer = QTimer(self)
        self.motion_timer.setSingleShot(True)
        self.motion_timer.setInterval(16)
        self.motion_timer.timeout.connect(self.flush_motion)
        manager.frame.connect(self.show_frame)

    def show_frame(self, path):
        self.image = QPixmap(path)
        self.redraw()

    def redraw(self):
        if not self.image.isNull():
            self.setPixmap(self.image.scaled(self.contentsRect().size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.redraw()

    def key(self, event, down):
        if event.isAutoRepeat():
            return
        special = {
            Qt.Key_Return: "ret", Qt.Key_Enter: "ret", Qt.Key_Escape: "esc",
            Qt.Key_Tab: "tab", Qt.Key_Backspace: "backspace", Qt.Key_Space: "spc",
            Qt.Key_Left: "left", Qt.Key_Right: "right", Qt.Key_Up: "up", Qt.Key_Down: "down",
            Qt.Key_Shift: "shift", Qt.Key_Control: "ctrl", Qt.Key_Alt: "alt",
            Qt.Key_Meta: "meta_l", Qt.Key_Delete: "delete", Qt.Key_Home: "home",
            Qt.Key_End: "end", Qt.Key_PageUp: "pgup", Qt.Key_PageDown: "pgdn",
            Qt.Key_Minus: "minus", Qt.Key_Equal: "equal", Qt.Key_Slash: "slash",
            Qt.Key_Period: "dot", Qt.Key_Comma: "comma", Qt.Key_Semicolon: "semicolon",
            Qt.Key_Apostrophe: "apostrophe", Qt.Key_BracketLeft: "bracket_left",
            Qt.Key_BracketRight: "bracket_right", Qt.Key_Backslash: "backslash",
            Qt.Key_QuoteLeft: "grave_accent",
        }
        # Qt reports the resulting symbol for shifted punctuation, whereas
        # QMP needs the base key (Shift is sent separately).
        shifted = dict(zip('!@#$%^&*()', "1234567890"))
        shifted.update({
            "_": "minus", "+": "equal", "?": "slash", ">": "dot",
            "<": "comma", ":": "semicolon", '"': "apostrophe",
            "{": "bracket_left", "}": "bracket_right", "|": "backslash",
            "~": "grave_accent",
        })
        code = special.get(event.key())
        if code is None and 0 <= event.key() <= 0x10ffff:
            code = shifted.get(chr(event.key()))
        if Qt.Key_A <= event.key() <= Qt.Key_Z or Qt.Key_0 <= event.key() <= Qt.Key_9:
            code = chr(event.key()).lower()
        if Qt.Key_F1 <= event.key() <= Qt.Key_F12:
            code = f"f{event.key() - Qt.Key_F1 + 1}"
        if code:
            self.manager.command("input-send-event", {"events": [
                {"type": "key", "data": {"down": down, "key": {"type": "qcode", "data": code}}}]})
        event.accept()

    def keyPressEvent(self, event):
        self.key(event, True)

    def keyReleaseEvent(self, event):
        self.key(event, False)

    def pointer_events(self, position, clamp=False):
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return None
        size = pixmap.deviceIndependentSize()
        area = QRectF(self.contentsRect()).adjusted(self.margin(), self.margin(),
                                                   -self.margin(), -self.margin())
        rect = QRectF(area.center().x() - size.width() / 2,
                      area.center().y() - size.height() / 2, size.width(), size.height())
        if not rect.contains(position) and not clamp:
            return None
        return [{"type": "abs", "data": {"axis": axis, "value": round(max(0, min(1, value)) * 32767)}}
                for axis, value in (("x", (position.x() - rect.left()) / rect.width()),
                                    ("y", (position.y() - rect.top()) / rect.height()))]

    def mouseMoveEvent(self, event):
        events = self.pointer_events(event.position(), bool(event.buttons()))
        self.pending_motion = events
        if events and not self.motion_timer.isActive():
            self.motion_timer.start()

    def flush_motion(self):
        if self.pending_motion:
            self.manager.command("input-send-event", {"events": self.pending_motion})
        self.pending_motion = None

    def leaveEvent(self, event):
        self.pending_motion = None
        super().leaveEvent(event)

    def mouse_button(self, event, down):
        self.motion_timer.stop()
        self.pending_motion = None
        events = self.pointer_events(event.position(), clamp=not down)
        button = {Qt.LeftButton: "left", Qt.RightButton: "right", Qt.MiddleButton: "middle"}.get(event.button())
        if events and button:
            self.setFocus()
            # Position and button share one QMP transaction, so a click cannot
            # overtake its matching movement on a scaled display.
            events.append({"type": "btn", "data": {"down": down, "button": button}})
            self.manager.command("input-send-event", {"events": events})

    def mousePressEvent(self, event):
        self.mouse_button(event, True)

    def mouseReleaseEvent(self, event):
        self.mouse_button(event, False)

import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6.QtWidgets')
from PySide6.QtCore import QObject, Signal, QPointF, Qt, QEvent
from PySide6.QtGui import QPixmap, QMouseEvent
from PySide6.QtWidgets import QApplication
from gui.preview_widget import Console


class Bridge(QObject):
    frame = Signal(str)
    def __init__(self):
        super().__init__()
        self.calls = []
    def command(self, name, arguments):
        self.calls.append(arguments['events'])


def test_hidpi_letterbox_pointer_and_atomic_click():
    app = QApplication.instance() or QApplication([])
    bridge = Bridge()
    console = Console(bridge)
    console.resize(1000, 800)
    image = QPixmap(1600, 900)
    image.setDevicePixelRatio(2)
    console.setPixmap(image)
    # Actual image occupies 800 x 450 logical pixels, centered at (500,400).
    assert [e['data']['value'] for e in console.pointer_events(QPointF(500,400))] == [16384,16384]
    assert [e['data']['value'] for e in console.pointer_events(QPointF(100,175))] == [0,0]
    assert console.pointer_events(QPointF(30,400)) is None
    assert [e['data']['value'] for e in console.pointer_events(QPointF(950,700), True)] == [32767,32767]
    event = QMouseEvent(QEvent.MouseButtonPress, QPointF(500,400), QPointF(500,400),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    console.mousePressEvent(event)
    assert len(bridge.calls) == 1
    assert [e['type'] for e in bridge.calls[0]] == ['abs','abs','btn']
    console.close()


def test_motion_burst_keeps_latest_position_before_click():
    app = QApplication.instance() or QApplication([])
    bridge = Bridge()
    console = Console(bridge)
    console.resize(1000,800)
    console.setPixmap(QPixmap(1000,800))
    for x in range(100,900):
        event = QMouseEvent(QEvent.MouseMove,QPointF(x,400),QPointF(x,400),Qt.NoButton,Qt.NoButton,Qt.NoModifier)
        console.mouseMoveEvent(event)
    assert not bridge.calls
    console.flush_motion()
    assert len(bridge.calls) == 1
    assert bridge.calls[0][0]['data']['value'] == round(899 / 1000 * 32767)
    console.close()


def test_compressed_live_frame_is_decoded():
    import base64,zlib
    from gui.live_editor import LiveEditor
    from PySide6.QtWidgets import QWidget
    app = QApplication.instance() or QApplication([])
    owner=QWidget()
    dialog=LiveEditor(owner)
    data=b'P6\n2 1\n255\n\xff\0\0\0\xff\0'
    dialog.event_received({'type':'frame','encoding':'ppm-zlib','data':base64.b64encode(zlib.compress(data)).decode()})
    assert dialog.console.image.size().width()==2
    assert dialog.console.image.toImage().pixelColor(0,0).red()==255
    dialog.finished_session(False)
    dialog.close()


def test_compressed_pointer_region_preserves_rest_of_desktop():
    import base64,zlib
    from gui.live_editor import LiveEditor
    from PySide6.QtWidgets import QWidget
    app=QApplication.instance() or QApplication([])
    owner=QWidget()
    dialog=LiveEditor(owner)
    full=b'P6\n2 1\n255\n\xff\0\0\0\xff\0'
    dialog.event_received({'type':'frame','data':base64.b64encode(full).decode()})
    patch=b'P6\n1 1\n255\n\0\0\xff'
    dialog.event_received({'type':'frame','encoding':'ppm-zlib','data':base64.b64encode(zlib.compress(patch)).decode(),
                           'region':{'x':1,'y':0,'canvas_width':2,'canvas_height':1}})
    image=dialog.console.image.toImage()
    assert image.pixelColor(0,0).red()==255
    assert image.pixelColor(1,0).blue()==255
    assert image.width()==2
    dialog.finished_session(False)
    dialog.close()

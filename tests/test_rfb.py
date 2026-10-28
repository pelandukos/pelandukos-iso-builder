import socket
import struct
import pytest
from core.errors import BuilderError
from services.rfb import Framebuffer


def test_shutdown_reset_does_not_turn_vm_exit_into_failure():
    class ResetSocket:
        def recv(self, count):
            raise ConnectionResetError("QEMU exited")
    reader = Framebuffer.__new__(Framebuffer)
    reader.socket = ResetSocket()
    assert reader.poll() is None


def connect(tmp_path):
    path = tmp_path / 'display.sock'
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(path))
    server.listen(1)
    reader = Framebuffer(path)
    peer, _ = server.accept()
    peer.settimeout(1)
    server.close()
    return reader, peer


def handshake(reader, peer):
    peer.sendall(b'RFB 003.')
    assert reader.poll() is None
    peer.sendall(b'008\n')
    reader.poll()
    assert peer.recv(12) == b'RFB 003.008\n'
    peer.sendall(b'\1\1')
    reader.poll()
    assert peer.recv(1) == b'\1'
    peer.sendall(b'\0' * 4)
    reader.poll()
    assert peer.recv(1) == b'\1'
    peer.sendall(struct.pack('>HH', 2, 1) + bytes(16) + struct.pack('>I', 4) + b'QEMU')
    reader.poll(request=True)
    assert len(peer.recv(1024)) == 42


def test_fragmented_colour_frame_and_resolution_change(tmp_path):
    reader, peer = connect(tmp_path)
    try:
        handshake(reader, peer)
        peer.sendall(struct.pack('>BBH', 0, 0, 1) + struct.pack('>HHHHi', 0, 0, 2, 1, 0) + b'\xff\0')
        assert reader.poll() is None
        peer.sendall(b'\0\0\0\xff\0\0')
        assert reader.poll() == b'P6\n2 1\n255\n\xff\0\0\0\xff\0'
        peer.sendall(struct.pack('>BBH', 0, 0, 2) + struct.pack('>HHHHi', 0, 0, 1, 1, -223)
                     + struct.pack('>HHHHi', 0, 0, 1, 1, 0) + b'\0\0\xff\0')
        assert reader.poll() == b'P6\n1 1\n255\n\0\0\xff'
    finally:
        reader.close()
        peer.close()


def test_rejects_framebuffer_out_of_bounds(tmp_path):
    reader, peer = connect(tmp_path)
    try:
        handshake(reader, peer)
        peer.sendall(struct.pack('>BBH', 0, 0, 1) + struct.pack('>HHHHi', 1, 0, 2, 1, 0))
        with pytest.raises(BuilderError, match='Invalid framebuffer rectangle'):
            reader.poll()
        with pytest.raises(BuilderError, match='exceeds'):
            reader.resize(4096, 2160)
    finally:
        reader.close()
        peer.close()


def test_incremental_patch_keeps_canvas_and_sends_small_region(tmp_path):
    reader, peer = connect(tmp_path)
    try:
        handshake(reader, peer)
        peer.sendall(struct.pack('>BBH', 0, 0, 1) + struct.pack('>HHHHi', 0, 0, 2, 1, 0)
                     + b'\xff\0\0\0\0\xff\0\0')
        reader.poll()
        reader.poll(request=True)
        assert peer.recv(10)[1] == 1  # Incremental update request
        peer.sendall(struct.pack('>BBH', 0, 0, 1) + struct.pack('>HHHHi', 1, 0, 1, 1, 0)
                     + b'\0\0\xff\0')
        assert reader.poll() == b'P6\n2 1\n255\n\xff\0\0\0\0\xff'
        data, region = reader.patch()
        assert data == b'P6\n1 1\n255\n\0\0\xff'
        assert region == {'x':1,'y':0,'canvas_width':2,'canvas_height':1}
    finally:
        reader.close()
        peer.close()


def test_large_dirty_tile_update(tmp_path):
    reader, peer = connect(tmp_path)
    try:
        handshake(reader, peer)
        tile = struct.pack('>HHHHi', 0, 0, 1, 1, 0) + b'\xff\0\0\0'
        peer.sendall(struct.pack('>BBH', 0, 0, 5000) + tile * 5000)
        assert reader.poll() == b'P6\n2 1\n255\n\xff\0\0\0\0\0'
        assert reader.pending is False
    finally:
        reader.close()
        peer.close()

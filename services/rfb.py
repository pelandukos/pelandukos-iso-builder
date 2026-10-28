"""Bounded nonblocking framebuffer reader for QEMU's private Unix VNC socket."""
import socket
import struct
from core.errors import BuilderError

MAX_PIXELS = 4096 * 1024


class Framebuffer:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.connect(str(path))
        self.socket.setblocking(False)
        self.buffer = bytearray()
        self.outgoing = bytearray()
        self.state = "version"
        self.width = self.height = 0
        self.pixels = bytearray()
        self.pending = False
        self.rectangles = 0
        self.rectangle = None
        self.changed = None
        self.need_full = True

    def close(self):
        self.socket.close()

    def resize(self, width, height):
        if not (0 < width <= 4096 and 0 < height <= 4096 and width * height <= MAX_PIXELS):
            raise BuilderError("VM framebuffer exceeds the supported size (4 megapixels).")
        self.need_full = True
        self.width, self.height = width, height
        self.pixels = bytearray(width * height * 3)

    def take(self, count):
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def poll(self, request=False):
        try:
            for _ in range(64):
                data = self.socket.recv(65536)
                if not data:
                    return None  # QEMU may close the socket during normal shutdown.
                self.buffer.extend(data)
        except BlockingIOError:
            pass
        except ConnectionResetError:
            return None  # Shutdown may reset rather than cleanly close VNC.
        frame = None
        while True:
            n = len(self.buffer)
            if self.state == "version":
                if n < 12: break
                if self.take(12) != b"RFB 003.008\n":
                    raise BuilderError("Unsupported VM framebuffer protocol.")
                self.outgoing.extend(b"RFB 003.008\n")
                self.state = "security"
            elif self.state == "security":
                if n < 1 or n < self.buffer[0] + 1: break
                types = self.take(self.buffer[0] + 1)[1:]
                if 1 not in types:
                    raise BuilderError("Unexpected authentication on private framebuffer socket.")
                self.outgoing.extend(b"\1")
                self.state = "authentication"
            elif self.state == "authentication":
                if n < 4: break
                if self.take(4) != b"\0" * 4:
                    raise BuilderError("Private framebuffer handshake failed.")
                self.outgoing.extend(b"\1")
                self.state = "initialization"
            elif self.state == "initialization":
                if n < 24: break
                length = struct.unpack(">I", self.buffer[20:24])[0]
                if length > 4096:
                    raise BuilderError("Invalid framebuffer server name.")
                if n < 24 + length: break
                header = self.take(24 + length)
                self.resize(*struct.unpack(">HH", header[:4]))
                # 32-bit little-endian truecolour, RGB in the first three bytes.
                pixel_format = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 0, 8, 16)
                self.outgoing.extend(b"\0\0\0\0" + pixel_format)
                self.outgoing.extend(struct.pack(">BBHii", 2, 0, 2, 0, -223))
                self.state = "message"
            elif self.state == "message":
                if n < 1: break
                kind = self.buffer[0]
                if kind == 2:  # Bell
                    self.take(1)
                    continue
                if kind != 0:
                    raise BuilderError("Unexpected framebuffer server message.")
                if n < 4: break
                self.changed = None
                self.rectangles = struct.unpack(">H", self.take(4)[2:])[0]
                # QEMU can split a full desktop into more than 4096 dirty
                # tiles. The protocol already bounds this count to uint16;
                # each rectangle is independently checked against the canvas.
                self.state = "rectangle"
            elif self.state == "rectangle":
                if not self.rectangles:
                    self.pending = False
                    frame = f"P6\n{self.width} {self.height}\n255\n".encode() + self.pixels
                    self.state = "message"
                    continue
                if n < 12: break
                x, y, w, h, encoding = struct.unpack(">HHHHi", self.take(12))
                if encoding == -223:
                    self.resize(w, h)
                    self.rectangles -= 1
                    continue
                if encoding != 0 or not w or not h or x + w > self.width or y + h > self.height:
                    raise BuilderError("Invalid framebuffer rectangle.")
                if self.changed is None:
                    self.changed = (x, y, x + w, y + h)
                else:
                    left, top, right, bottom = self.changed
                    self.changed = (min(left,x), min(top,y), max(right,x+w), max(bottom,y+h))
                self.rectangle = x, y, w, h
                self.state = "pixels"
            elif self.state == "pixels":
                x, y, w, h = self.rectangle
                if n < w * h * 4: break
                raw = self.take(w * h * 4)
                rgb = bytearray(w * h * 3)
                for channel in range(3):
                    rgb[channel::3] = raw[channel::4]
                for row in range(h):
                    offset = ((y + row) * self.width + x) * 3
                    self.pixels[offset:offset + w * 3] = rgb[row * w * 3:(row + 1) * w * 3]
                self.rectangles -= 1
                self.state = "rectangle"
        if request and self.state == "message" and not self.pending:
            self.outgoing.extend(struct.pack(">BBHHHH", 3, 0 if self.need_full else 1, 0, 0, self.width, self.height))
            self.pending = True
            self.need_full = False
        if self.outgoing:
            try:
                sent = self.socket.send(self.outgoing)
                del self.outgoing[:sent]
            except BlockingIOError:
                pass
            except (BrokenPipeError, ConnectionResetError):
                return None
        return frame

    def patch(self):
        if self.changed is None:
            return None
        x, y, right, bottom = self.changed
        width, height = right - x, bottom - y
        data = b"".join(self.pixels[(row*self.width+x)*3:(row*self.width+right)*3]
                        for row in range(y, bottom))
        return (f"P6\n{width} {height}\n255\n".encode() + data,
                {"x": x, "y": y, "canvas_width": self.width, "canvas_height": self.height})

"""Serial link to the LCR-P1 (CH340, 115200 8N1) and a background reader that turns the
meter's text stream into records."""
import fcntl
import glob
import os
import select
import struct
import termios
import threading
import time

from .parse import Parser

CH340 = ('1a86', '7523')


def find_port():
    """/dev/ttyUSBn of the meter's CH340, or None. The CH340 only appears while the meter is on."""
    for tty in sorted(glob.glob('/sys/class/tty/ttyUSB*')):
        d = os.path.realpath(os.path.join(tty, 'device'))
        for _ in range(4):                      # interface -> usb device holds idVendor
            try:
                vid = open(os.path.join(d, 'idVendor')).read().strip()
                pid = open(os.path.join(d, 'idProduct')).read().strip()
            except OSError:
                d = os.path.dirname(d)
                continue
            if (vid, pid) == CH340:
                return '/dev/' + os.path.basename(tty)
            break
    return None


def open_port(dev):
    """Open raw 115200 8N1 with DTR/RTS low (as FNIRSI's Windows tool does)."""
    fd = os.open(dev, os.O_RDWR | os.O_NOCTTY)
    a = termios.tcgetattr(fd)
    a[0] = a[1] = a[3] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    a[4] = a[5] = termios.B115200
    a[6][termios.VMIN] = 0
    a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    fcntl.ioctl(fd, termios.TIOCMBIC, struct.pack('I', termios.TIOCM_DTR | termios.TIOCM_RTS))
    termios.tcflush(fd, termios.TCIFLUSH)
    return fd


class Pacer:
    """Sends command bytes one at a time: the meter's USART holds a single byte and the firmware
    reads it once per main-loop pass, so each byte goes out only after the meter echoed the
    previous one (echo = byte | 0x80, never part of the ASCII screen text)."""

    WAIT, TRIES = 3.0, 2            # a running test pauses the main loop for ~1-3 s

    def __init__(self):
        self.queue = bytearray()
        self.waiting = None         # (byte, deadline, tries)
        self.lock = threading.Lock()

    def add(self, cmd):
        with self.lock:
            self.queue += f'#{cmd}\n'.encode()

    def busy(self):
        return bool(self.queue or self.waiting)

    def feed(self, data):
        """Take incoming bytes; returns them without echoes."""
        if self.waiting and (self.waiting[0] | 0x80) in data:
            self.waiting = None
        return bytes(b for b in data if b < 0x80)

    def pump(self, fd):
        """Send the next byte if allowed."""
        now = time.time()
        with self.lock:
            if self.waiting:
                b, deadline, tries = self.waiting
                if now < deadline:
                    return
                if tries >= self.TRIES:     # meter not answering (old firmware?): drop the line
                    self.waiting = None
                    i = self.queue.find(b'\n')
                    del self.queue[:i + 1 if i >= 0 else len(self.queue)]
                    return
                os.write(fd, bytes([b]))
                self.waiting = (b, now + self.WAIT, tries + 1)
                return
            if self.queue:
                b = self.queue.pop(0)
                os.write(fd, bytes([b]))
                self.waiting = (b, now + self.WAIT, 1)


class Reader(threading.Thread):
    """Finds the meter, reads it, reconnects after power cycles.

    on_record(rec) gets each parsed record; on_status(text, connected) the link state;
    on_raw(bytes) every chunk read. All callbacks run on this thread.
    """

    def __init__(self, on_record, on_status, on_raw=None, port=None):
        super().__init__(daemon=True)
        self.on_record, self.on_status, self.on_raw = on_record, on_status, on_raw
        self.fixed_port = port
        self._stop = threading.Event()
        self._paused = threading.Event()
        self.idle = threading.Event()           # set while the port is closed (for flashing)
        self.idle.set()
        self.fd = None
        self._wlock = threading.Lock()
        self.pacer = Pacer()

    def send(self, cmd):
        """Queue a command for the meter, e.g. 'T', 'R5', 'A300', 'O', '?'. Firmware with serial
        commands answers '#<cmd> R=.. A=.. P=..'. Returns False when not connected."""
        if self.fd is None:
            return False
        self.pacer.add(cmd)
        return True

    def stop(self):
        self._stop.set()

    def pause(self, timeout=3.0):
        """Close the port and wait (e.g. to flash firmware). Returns True when released."""
        self._paused.set()
        return self.idle.wait(timeout)

    def resume(self):
        self._paused.clear()

    def run(self):
        parser = Parser(self.on_record)
        last_state = None
        while not self._stop.is_set():
            if self._paused.is_set():
                if last_state != 'paused':
                    self.on_status('Paused (port released)', False)
                    last_state = 'paused'
                time.sleep(0.2)
                continue
            dev = self.fixed_port or find_port()
            if not dev or not os.path.exists(dev):
                if last_state != 'wait':
                    self.on_status('Waiting for the meter (switch it on, USB connected)', False)
                    last_state = 'wait'
                time.sleep(0.5)
                continue
            try:
                fd = open_port(dev)
            except OSError as e:
                if last_state != 'err':
                    self.on_status(f'Cannot open {dev}: {e.strerror}', False)
                    last_state = 'err'
                time.sleep(1.0)
                continue
            self.idle.clear()
            with self._wlock:
                self.fd = fd
                self.pacer = Pacer()
            self.on_status(f'Connected on {dev}', True)
            last_state = 'conn'
            buf, last = '', time.time()
            try:
                while not self._stop.is_set() and not self._paused.is_set():
                    self.pacer.pump(fd)
                    r, _, _ = select.select([fd], [], [], 0.02 if self.pacer.busy() else 0.2)
                    if r:
                        data = os.read(fd, 1024)
                        if not data:
                            raise OSError('meter disconnected')
                        data = self.pacer.feed(data)
                        if not data:
                            continue
                        if self.on_raw:
                            self.on_raw(data)
                        buf += data.decode('latin-1')
                        last = time.time()
                        *lines, buf = buf.split('\n')
                        for ln in lines:
                            parser.line(ln)
                    elif time.time() - last > 0.8:
                        if buf:                     # result screens end without a newline
                            parser.line(buf)
                            buf = ''
                        parser.flush()
                    if not os.path.exists(dev):
                        raise OSError('meter disconnected')
            except OSError:
                pass
            finally:
                parser.line(buf)
                parser.flush()
                with self._wlock:
                    self.fd = None
                os.close(fd)
                self.idle.set()
            if not self._paused.is_set() and not self._stop.is_set():
                self.on_status('Meter disconnected', False)
                last_state = 'gone'
                time.sleep(0.5)

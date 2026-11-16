"""LCR-P1 firmware update: FNIRSI's IAP bootloader or the LCR-P1 bootloader (lcr-p1-fw boot/).

FNIRSI protocol (recovered from BootTool.exe):
  1. 115200 8N1: send AC 55 02 03, close, wait 10 ms, reopen
  2. send the image in 512-byte blocks; the last block is padded with 0xFF and ends with
     78 56 34 12 (an extra marker block if the image fills its last block); no acks
  3. reply within 20 s: [1] == 0x55 and [2] == 0x03 means success (seen: ac 55 03 04)
LCR-P1 bootloader: after the same header it stays quiet for 500 ms (FNIRSI hosts have started sending
blocks by then), then sends a hello (an INFO reply). Then framed commands with CRC32 and replies:
  host  A5 cmd len16 payload crc32(cmd..payload)      meter  5A cmd status len16 payload crc32(cmd..payload)
  1 INFO, 2 READ addr len16, 3 CRC addr len, 5 WRITE addr+512 B, 6 COMMIT len crc32, 7 BOOT, 8 OFF,
  9 BAUD rate, 10 BEGIN len. The app's first page is held back until COMMIT has checked the whole image.
Update mode: hold ZENER, then press TEST; with the LCR-P1 bootloader and lcrp1-fw, `#U` also gets there.
"""
import os
import select
import struct
import termios
import time
import zlib

from .port import open_port

BLOCK = 512
MARK = bytes([0x78, 0x56, 0x34, 0x12])
STOCK_LEN = 59276          # APP_LCR-P1-EN_V1.1.0_260324.BIN
APP_BASE = 0x08001800      # app is linked here; FNIRSI bootloader owns 0x08000000-0x080017FF
CAL_ADDR = 0x0800FFD0      # calibration record ("dEnK" + 32 bytes)


def blocks(img):
    out = []
    for off in range(0, len(img), BLOCK):
        b = bytearray(b'\xff' * BLOCK)
        b[BLOCK - 4:] = MARK
        chunk = img[off:off + BLOCK]
        b[:len(chunk)] = chunk
        out.append(bytes(b))
    if len(img) % BLOCK == 0 or len(img) % BLOCK >= 509:
        out.append(b'\xff' * (BLOCK - 4) + MARK)
    return out


def check(img):
    """Sanity checks; returns a list of problems (empty = OK)."""
    errs = []
    if len(img) < 8:
        return ['file too small']
    sp, rst = struct.unpack_from('<II', img, 0)
    if not 0x20000000 < sp < 0x20002000:
        # the bootloader starts an app only if (SP & 0x0FFFFFFF) < 0x2000; otherwise it stays in update
        # mode, holding the power latch (only a battery disconnect switches the meter off then)
        errs.append(f'initial SP {sp:#x} must be below 0x20002000 or the bootloader will not start it')
    if not (rst & 1 and APP_BASE <= rst < APP_BASE + len(img)):
        errs.append(f'reset vector {rst:#x} outside the image: not an LCR-P1 app image?')
    bl = blocks(img)
    for i, b in enumerate(bl[:-1]):
        if b[-4:] == MARK:
            errs.append(f'block {i} ends with the end marker: the bootloader would stop early')
    if len(img) > STOCK_LEN:
        errs.append(f'image is {len(img) - STOCK_LEN} bytes larger than stock')
    if APP_BASE + len(img) > CAL_ADDR:
        errs.append('image would overlap the calibration record at 0x0800FFD0')
    return errs


HEADER = bytes([0xAC, 0x55, 0x02, 0x03])
ST = {0: 'ok', 1: 'frame CRC error', 2: 'unknown command', 3: 'bad arguments', 4: 'flash error',
      5: 'image CRC mismatch', 6: 'no valid app'}
INFO_FMT = '<6sBBIIHHIBBB'


class BLError(Exception):
    pass


def _read(fd, timeout):
    r, _, _ = select.select([fd], [], [], timeout)
    return os.read(fd, 4096) if r else b''


def _reply(buf):
    """(cmd, status, payload, rest) from the start of buf, None while incomplete; skips junk before 5A"""
    while buf and buf[0] != 0x5A:
        buf = buf[1:]
    if len(buf) < 9:
        return None
    n = buf[3] | buf[4] << 8
    if len(buf) < 9 + n:
        return None
    body = buf[5:5 + n]
    if struct.unpack_from('<I', buf, 5 + n)[0] != zlib.crc32(buf[1:5] + body):
        return 'bad'
    return buf[1], buf[2], body, buf[9 + n:]


def _wait_reply(fd, timeout):
    buf, end = b'', time.time() + timeout
    while time.time() < end:
        buf += _read(fd, 0.05)
        r = _reply(buf)
        if r == 'bad':
            return None
        if r:
            return r
    return None


def call(fd, cmd, payload=b'', timeout=2.0, tries=3):
    """One framed command with retries; returns the reply payload, raises BLError."""
    h = bytes([cmd]) + struct.pack('<H', len(payload))
    frame = b'\xA5' + h + payload + struct.pack('<I', zlib.crc32(h + payload))
    last = 'no reply'
    for _ in range(tries):
        termios.tcflush(fd, termios.TCIFLUSH)
        os.write(fd, frame)
        r = _wait_reply(fd, timeout)
        if r is None:
            continue
        rcmd, st, body, _ = r
        if rcmd != cmd:
            last = f'reply to {rcmd:#x}'
            continue
        if st == 1:                 # frame damaged on the way: send again
            last = ST[1]
            continue
        if st:
            raise BLError(f'command {cmd}: {ST.get(st, st)}')
        return body
    raise BLError(f'command {cmd}: {last}')


def parse_info(body):
    f = struct.unpack(INFO_FMT, body)
    return {'version': f'{f[1]}.{f[2]}', 'app': f[3], 'end': f[4], 'page': f[5], 'crc': f[7],
            'valid': bool(f[8]), 'entry': ('ZENER key', 'asked by app', 'no valid app')[f[9]] if f[9] < 3 else f[9]}


def set_baud(fd, baud):
    a = termios.tcgetattr(fd)
    a[4] = a[5] = getattr(termios, f'B{baud}')
    termios.tcdrain(fd)
    termios.tcsetattr(fd, termios.TCSANOW, a)


def hello(fd):
    """Send the FNIRSI header; returns the LCR-P1 bootloader's INFO dict, or None (FNIRSI loader, silent)."""
    termios.tcflush(fd, termios.TCIFLUSH)
    os.write(fd, HEADER)
    termios.tcdrain(fd)
    r = _wait_reply(fd, 0.9)
    if r and r[0] == 0 and r[1] == 0 and r[2][:6] == b'LCRPBL':
        return parse_info(r[2])
    return None


def ask_bootloader(fd, heard):
    """The meter runs a firmware: lcrp1-fw hands over to the LCR-P1 bootloader on #U. Returns (ok, text)."""
    if b'#' not in heard:
        return False, 'not lcrp1-fw'
    termios.tcflush(fd, termios.TCIFLUSH)
    for b in b'#U\r':
        os.write(fd, bytes([b]))
        time.sleep(0.02)
    got, end = b'', time.time() + 2
    while time.time() < end and not any(l.startswith(b'#U ') for l in got.split(b'\n')[:-1]):
        got += _read(fd, 0.1)          # until a complete line starting with "#U " arrived
    line = next((l for l in got.decode('latin-1', 'replace').splitlines() if l.startswith('#U ')), '')
    if line.startswith('#U bootloader'):
        time.sleep(0.8)             # it sets up the screen first
        termios.tcflush(fd, termios.TCIFLUSH)
        return True, line
    return False, line or 'no answer to #U'


def flash_framed(fd, img, info, progress=None, baud=None):
    """Write img with the LCR-P1 bootloader (fd: after hello). Returns a message, raises BLError."""
    if baud:
        call(fd, 9, struct.pack('<I', baud))
        set_baud(fd, baud)
        time.sleep(0.05)
        call(fd, 1)                 # within 1 s, else the meter falls back to 115200
    pages = (len(img) + BLOCK - 1) // BLOCK
    call(fd, 10, struct.pack('<I', len(img)), timeout=3)
    order = list(range(1, pages)) + [0]          # page 0 is held in RAM until COMMIT anyway
    for k, i in enumerate(order):
        chunk = img[i * BLOCK:(i + 1) * BLOCK].ljust(BLOCK, b'\xff')
        call(fd, 5, struct.pack('<I', APP_BASE + i * BLOCK) + chunk, timeout=3)
        if progress:
            progress(k + 1, pages)
    call(fd, 6, struct.pack('<II', len(img), zlib.crc32(img)), timeout=5)
    call(fd, 7)
    return f'Update successful (LCR-P1 bootloader {info["version"]}, image CRC {zlib.crc32(img):08x} verified)'


def backup(dev, out_path, progress=None):
    """Read the whole 64 KB flash (bootloader, app, calibration pages) with the LCR-P1 bootloader."""
    fd = open_port(dev)
    try:
        info = hello(fd)
        if not info:
            return False, 'No LCR-P1 bootloader answered (put the meter in update mode first).'
        data = bytearray()
        for a in range(0x08000000, 0x08010000, BLOCK):
            data += call(fd, 2, struct.pack('<IH2x', a, BLOCK))
            if progress:
                progress(len(data) // BLOCK, 128)
        crc = struct.unpack('<I', call(fd, 3, struct.pack('<II', 0x08000000, 0x10000)))[0]
        if crc != zlib.crc32(bytes(data)):
            return False, 'CRC of the read data does not match the meter: not saved'
        open(out_path, 'wb').write(data)
        return True, f'Saved 64 KB to {out_path} (CRC {crc:08x}); meter stays in update mode'
    except BLError as e:
        return False, str(e)
    finally:
        os.close(fd)


def flash(img, dev, progress=None, listen=1.2, baud=None):
    """Send img; progress(done, total) per block. Returns (ok, message).

    The bootloader is silent in update mode. If the meter sends anything during the first `listen`
    seconds it is running a firmware instead (e.g. lcrp1-fw prints a status line every second): then
    nothing is sent, so the image cannot end up as input for the running firmware."""
    fd = open_port(dev)
    heard, end = b'', time.time() + listen
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            heard += os.read(fd, 256)
    if heard:
        ok, why = ask_bootloader(fd, heard)
        if not ok:
            os.close(fd)
            text = heard[:40].decode('latin-1', 'replace').replace('\r', ' ').replace('\n', ' ').strip()
            return False, (f'The meter is running its firmware, not in update mode (it sent "{text}"; {why}). '
                           'Nothing was sent. Switch it off, hold ZENER, then press TEST.')
    info = hello(fd)
    if info:
        try:
            return True, flash_framed(fd, img, info, progress, baud)
        except BLError as e:
            return False, f'Update failed: {e}. The old app is invalidated only once writing started; the meter stays in update mode, run the update again.'
        finally:
            os.close(fd)
    os.close(fd)
    time.sleep(0.01)
    fd = open_port(dev)
    try:
        bl = blocks(img)
        for i, b in enumerate(bl):
            os.write(fd, b)
            termios.tcdrain(fd)
            if progress:
                progress(i + 1, len(bl))
        reply = b''
        end = time.time() + 20
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.2)
            if r:
                reply += os.read(fd, 64)
                if len(reply) >= 3:
                    time.sleep(0.05)
                    break
    finally:
        os.close(fd)
    if len(reply) >= 3 and reply[1] == 0x55 and reply[2] == 0x03:
        return True, f'Update successful (reply {reply.hex(" ")})'
    if reply:
        return False, f'Update failed (reply {reply.hex(" ")})'
    return False, 'No reply: was the meter in update mode (hold ZENER, press TEST)? Nothing was written.'


def main(argv=None):
    """lcrp1 flash [--check] FILE [PORT]: update the meter from the command line."""
    import argparse
    import sys
    from .port import find_port
    ap = argparse.ArgumentParser(prog='lcrp1 flash', description=main.__doc__)
    ap.add_argument('--check', action='store_true', help='only check the image, do not flash')
    ap.add_argument('--backup', action='store_true', help='save the whole flash to FILE (LCR-P1 bootloader)')
    ap.add_argument('--baud', type=int, help='faster link for the LCR-P1 bootloader, e.g. 460800')
    ap.add_argument('file')
    ap.add_argument('port', nargs='?')
    a = ap.parse_args(argv)
    if a.backup:
        dev = a.port or find_port()
        if not dev:
            print('Meter not found.')
            return 1
        ok, msg = backup(dev, a.file, lambda i, n: print(f'\rread {i}/{n} pages', end='', flush=True))
        print()
        print(msg)
        return 0 if ok else 1
    img = open(a.file, 'rb').read()
    errs = check(img)
    for e in errs:
        print('problem:', e)
    print(f'{a.file}: {len(img)} bytes, {len(blocks(img))} blocks, {"OK" if not errs else "NOT OK"}')
    if errs:
        return 1
    if a.check:
        return 0
    dev = a.port or find_port()
    if not dev:
        print('Meter not found. Switch it on in update mode (hold ZENER, press TEST), USB connected.')
        return 1
    ok, msg = flash(img, dev, lambda i, n: print(f'\rsent {i}/{n} blocks', end='', flush=True), baud=a.baud)
    print()
    print(msg)
    return 0 if ok else 1

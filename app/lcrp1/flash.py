"""LCR-P1 firmware update over FNIRSI's IAP bootloader (protocol recovered from BootTool.exe).

  1. 115200 8N1: send AC 55 02 03, close, wait 10 ms, reopen
  2. send the image in 512-byte blocks; the last block is padded with 0xFF and ends with
     78 56 34 12 (an extra marker block if the image fills its last block); no acks
  3. reply within 20 s: [1] == 0x55 and [2] == 0x03 means success (seen: ac 55 03 04)
The meter must be in update mode first: hold ZENER, then press TEST.
"""
import os
import select
import struct
import termios
import time

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
    if not 0x20000000 < sp <= 0x20002000:
        errs.append(f'initial SP {sp:#x} looks wrong: not an LCR-P1 app image?')
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


def flash(img, dev, progress=None):
    """Send img; progress(done, total) per block. Returns (ok, message)."""
    fd = open_port(dev)
    os.write(fd, bytes([0xAC, 0x55, 0x02, 0x03]))
    termios.tcdrain(fd)
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
    ap.add_argument('file')
    ap.add_argument('port', nargs='?')
    a = ap.parse_args(argv)
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
    ok, msg = flash(img, dev, lambda i, n: print(f'\rsent {i}/{n} blocks', end='', flush=True))
    print()
    print(msg)
    return 0 if ok else 1

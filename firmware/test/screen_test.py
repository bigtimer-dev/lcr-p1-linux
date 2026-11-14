# Boot screen and colour remap of the patched firmware.
#  - runs the stock splash routine (0x0800DE50) with the stock bitmap renderer; pixels go through
#    the patch's pixel_hook into a stub LCD; compares with the art file
#  - checks the pixel colour remap
# usage: screen_test.py patched.bin patch.elf
import os
import struct
import subprocess
import sys

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, \
    UC_ARM_REG_R3, UC_ARM_REG_SP

BASE = 0x08001800
SPLASH, WINDOW, PIXEL, DRAWSTR, FILL, DELAY = 0x0800DE50, 0x08005B7C, 0x08006060, 0x080060C0, 0x08005740, 0x0800A1D4
ACCENT, LABEL, DIM = 0xB47F, 0x8C33, 0x4A2A
img = open(sys.argv[1], 'rb').read()
syms = {l.split()[-1]: int(l.split()[0], 16) for l in
        subprocess.check_output(['arm-none-eabi-nm', sys.argv[2]], text=True).splitlines()}
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'art'))
art = __import__(os.environ.get('BOOT_ART', 'skull'))

uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
uc.mem_map(0x08000000, 0x10000); uc.mem_write(BASE, img); uc.mem_map(0x20000000, 0x2000)
lcd = {}
win = {'x0': 0, 'x1': 0, 'x': 0, 'y': 0}
texts = []
regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)

def ret(uc):
    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))

def hk(uc, a, sz, _):
    r = [uc.reg_read(x) for x in regs]
    if a == WINDOW:
        win.update(x0=r[0], x1=r[2], x=r[0], y=r[1])
    elif a == PIXEL:
        lcd[(win['x'], win['y'])] = r[0] & 0xFFFF
        win['x'] += 1
        if win['x'] > win['x1']:
            win['x'] = win['x0']; win['y'] += 1
    elif a == DRAWSTR:
        s = bytes(uc.mem_read(r[2], 16)).split(b'\0')[0].decode()
        if s:
            texts.append((r[0], r[1], s, r[3] & 0xFFFF))
    elif a in (FILL, DELAY):
        pass
    else:
        return
    ret(uc)
for a in (WINDOW, PIXEL, DRAWSTR, FILL, DELAY):
    uc.hook_add(UC_HOOK_CODE, hk, begin=a, end=a)

def call(fn, *args):
    for reg, v in zip(regs, args):
        uc.reg_write(reg, v)
    uc.reg_write(UC_ARM_REG_SP, 0x20001800); uc.reg_write(UC_ARM_REG_LR, 0x08000001)
    uc.emu_start(fn | 1, 0x08000000, count=2_000_000)

ok = True
call(SPLASH)
W, H = len(art.ROWS[0]), len(art.ROWS)
x0, y0 = (128 - W) // 2, 40
drawn = {p for p, c in lcd.items() if c != 0}
want = {(x0 + x, y0 + y) for y, row in enumerate(art.ROWS) for x, ch in enumerate(row) if ch == '#'}
cols = {c for p, c in lcd.items() if c != 0}
for y in range(y0 - 2, y0 + H + 1):
    print(''.join('██' if (x, y) in drawn else '  ' for x in range(x0 - 2, x0 + W + 2)).rstrip())
good = drawn == want and cols == {ACCENT}
ok &= good
print(f"{'PASS' if good else 'FAIL'}  boot picture: {W}x{H} at ({x0},{y0}), purple, via stock splash + bitmap code")
good = texts == [((128 - 30) // 2, 0x69, '1.1.0', LABEL)]
ok &= good
print(f"{'PASS' if good else 'FAIL'}  version line: only '1.1.0', centred, grey  {texts}")

# colour remap at the pixel sink
cases = {0x0000: 0x0000, 0xFFFF: 0xFFFF, ACCENT: ACCENT, LABEL: LABEL, 0x07E0: ACCENT, 0xFFE0: ACCENT,
         0x04DB: ACCENT, 0x001F: ACCENT, 0xF800: 0xF800, 0xA534: DIM, 0x8410: DIM, DIM: DIM, 0x2104: DIM}
bad = []
for c, want_c in cases.items():
    lcd.clear(); win.update(x0=0, x1=10, x=0, y=0)
    call(syms['pixel_hook'], c)
    if lcd.get((0, 0)) != want_c:
        bad.append((hex(c), hex(lcd.get((0, 0)) or 0), hex(want_c)))
ok &= not bad
print(f"{'PASS' if not bad else 'FAIL'}  colour remap: colours -> purple, greys -> dim, black/white/red kept {bad}")
sys.exit(0 if ok else 1)

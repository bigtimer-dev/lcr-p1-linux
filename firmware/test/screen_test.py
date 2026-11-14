# Screen output of the patched firmware, end to end through the real LCD code:
#  - runs the firmware's startup data initialisation (so the LCD config in RAM is real)
#  - runs the stock splash / window / cursor / pixel / character routines with the patch's hooks
#  - a model LCD decodes the command + data stream (column set, row set, memory write) into a
#    framebuffer, which is compared with the boot art and with the font
# Only the SPI byte output, the LCD command write, UART output and delays are stubbed; the LCD
# orientation/command table is set up by the firmware's own routine (0x08005574, argument 1).
# usage: screen_test.py patched.bin patch.elf
import os
import struct
import subprocess
import sys

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, \
    UC_ARM_REG_R3, UC_ARM_REG_SP

BASE = 0x08001800
SCATTER, AFTER_SCATTER = 0x0800218C, 0x080018C8       # __scatterload, then __main -> main
SPLASH, DRAWSTR = 0x0800DE50, 0x080060C0
LCD_CMD, SPI_BYTE, PUTC, DELAY_MS, FILL = 0x08006090, 0x0800720C, 0x0800B2DC, 0x0800A1D4, 0x08005740
LCD_CFG = 0x200008A4                                    # +5 RAMWR, +6 CASET, +7 RASET, +8/+9 x/y offset
FONT = 0x0800F01A
ACCENT, LABEL, DIM, WHITE = 0xB47F, 0x8C33, 0x4A2A, 0xFFFF
img = open(sys.argv[1], 'rb').read()
syms = {l.split()[-1]: int(l.split()[0], 16) for l in
        subprocess.check_output(['arm-none-eabi-nm', sys.argv[2]], text=True).splitlines()}
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'art'))
art = __import__(os.environ.get('BOOT_ART', 'mask'))

uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
uc.mem_map(0x08000000, 0x10000); uc.mem_write(BASE, img)
uc.mem_map(0x20000000, 0x2000); uc.mem_map(0x40000000, 0x30000)
regs = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)


class LCD:
    def __init__(self):
        self.fb, self.cmd, self.data = {}, None, []
        self.x0 = self.x1 = self.y0 = self.y1 = self.x = self.y = 0
        self.bad = 0

    def command(self, c):
        cfg = bytes(uc.mem_read(LCD_CFG, 10))
        self.ramwr, self.caset, self.raset, self.xo, self.yo = cfg[5], cfg[6], cfg[7], cfg[8], cfg[9]
        self.cmd, self.data = c, []
        if c == self.ramwr:
            self.x, self.y = self.x0, self.y0

    def byte(self, b):
        self.data.append(b)
        if len(self.data) % 2:
            return
        w = self.data[-2] << 8 | self.data[-1]
        n = len(self.data) // 2
        if self.cmd == self.caset:
            if n == 1: self.x0 = w - self.xo
            else: self.x1 = w - self.xo
        elif self.cmd == self.raset:
            if n == 1: self.y0 = w - self.yo
            else: self.y1 = w - self.yo
        elif self.cmd == self.ramwr:
            if 0 <= self.x < 128 and 0 <= self.y < 128:
                self.fb[(self.x, self.y)] = w
            else:
                self.bad += 1
            self.x += 1
            if self.x > self.x1:
                self.x, self.y = self.x0, self.y + 1


lcd = LCD()

def hk(uc, a, sz, _):
    r0 = uc.reg_read(UC_ARM_REG_R0)
    if a == LCD_CMD:
        lcd.command(r0 & 0xFF)
    elif a == SPI_BYTE:
        lcd.byte(r0 & 0xFF)
    elif a in (PUTC, DELAY_MS, FILL):
        pass
    else:
        return
    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
for a in (LCD_CMD, SPI_BYTE, PUTC, DELAY_MS, FILL):
    uc.hook_add(UC_HOOK_CODE, hk, begin=a, end=a)

def call(fn, args=(), stack=()):
    sp = 0x20001800 - 4 * len(stack)
    uc.mem_write(sp, struct.pack(f'<{len(stack)}I', *stack))
    for reg, v in zip(regs, args):
        uc.reg_write(reg, v)
    uc.reg_write(UC_ARM_REG_SP, sp); uc.reg_write(UC_ARM_REG_LR, 0x08000001)
    uc.emu_start(fn | 1, 0x08000000, count=3_000_000)

# startup: copy/decompress RW data, zero ZI (gives the real LCD config and GPIO pointers)
uc.reg_write(UC_ARM_REG_SP, 0x200018C8)
uc.emu_start(SCATTER | 1, AFTER_SCATTER, count=2_000_000)
call(0x08005574, (1,))     # LCD orientation setup, as the firmware's LCD init calls it (0x08005A22)
cfg = bytes(uc.mem_read(LCD_CFG, 10))
print('LCD config from RW data: RAMWR %02x CASET %02x RASET %02x offset %d,%d' % (cfg[5], cfg[6], cfg[7], cfg[8], cfg[9]))

ok = True
def check(name, good, info=''):
    global ok
    ok &= bool(good)
    print(f"{'PASS' if good else 'FAIL'}  {name}  {info}")

def text_pixels(s, x, y):
    out = {}
    for i, ch in enumerate(s):
        g = img[FONT - BASE + (ord(ch) - 32) * 12:][:12]
        for r, row in enumerate(g):
            for b in range(6):
                out[(x + i * 6 + b, y + r)] = bool(row >> b & 1)
    return out

# 1) boot picture through the stock splash, window and bitmap code
call(SPLASH)
W, H = len(art.ROWS[0]), len(art.ROWS)
x0, y0 = (128 - W) // 2, 40
lit = {p for p, c in lcd.fb.items() if c != 0 and p[1] < 0x69}          # above the version line
ver = {p: c for p, c in lcd.fb.items() if c != 0 and p[1] >= 0x69}
want = {(x0 + x, y0 + y) for y, row in enumerate(art.ROWS) for x, ch in enumerate(row) if ch == '#'}
for y in range(y0 - 1, y0 + H):
    print(''.join('██' if (x, y) in lit else '  ' for x in range(x0 - 1, x0 + W + 1)).rstrip())
check('boot picture drawn where expected, nothing outside', lit == want and lcd.bad == 0,
      f'{len(lit)} lit / {len(want)} expected, {lcd.bad} off-screen')
check('boot picture is purple', {lcd.fb[p] for p in lit} == {ACCENT})
ver_want = {p for p, on in text_pixels('1.1.0', (128 - 30) // 2, 0x69).items() if on}
check('version line: only "1.1.0", centred, grey', set(ver) == ver_want and set(ver.values()) == {LABEL},
      f'{len(ver)} px')

# 2) text through the real draw_string -> draw_char -> pixel path (mode 0: fg + bg pixels)
STR = 0x20001000
for s, x, y, colour in (('Cap:', 5, 20, LABEL), ('4.7', 40, 20, ACCENT), ('Resistance', 5, 60, WHITE)):
    lcd.fb.clear()
    uc.mem_write(STR, s.encode() + b'\0')
    call(DRAWSTR, (x, y, STR, WHITE), (0, 12, 0))
    exp = text_pixels(s, x, y)
    got_fg = {p for p, c in lcd.fb.items() if c == colour}
    got_bg = {p for p, c in lcd.fb.items() if c == 0}
    good = got_fg == {p for p, on in exp.items() if on} and got_bg == {p for p, on in exp.items() if not on}
    check(f'text "{s}" pixel-exact in {colour:#06x}', good and lcd.bad == 0, f'{len(lcd.fb)} px')

# 3) idle screen pin legend (0x0800DEDC): no panel, grey digits, the '?' icon purple
lcd.fb.clear()
call(0x0800DEDC)
cols = {}
for p, c in lcd.fb.items():
    cols.setdefault(c, set()).add(p)
legend = {p for p in cols.get(LABEL, set())}
check('idle legend: only black, grey digits and the purple ? icon', set(cols) <= {0, LABEL, ACCENT},
      {hex(c): len(v) for c, v in cols.items()})
want = set()
for x, y, ch in ((0x14, 0x55, '1'), (0x21, 0x55, '2'), (0x2d, 0x55, '3'), (0x14, 0x67, 'K'), (0x21, 0x67, 'A')):
    want |= {p for p, on in text_pixels(ch, x, y).items() if on}
check('idle legend: digits drawn in place', want <= legend, f'{len(legend)} grey px')
check('idle legend: nothing outside the screen', lcd.bad == 0)

# 4) colour remap at the pixel sink (called directly)
cases = {0x0000: 0x0000, 0xFFFF: 0xFFFF, ACCENT: ACCENT, LABEL: LABEL, 0x07E0: ACCENT, 0xFFE0: ACCENT,
         0x04DB: ACCENT, 0x001F: ACCENT, 0xF800: 0xF800, 0xA534: DIM, 0x8410: DIM, DIM: DIM, 0x2104: DIM}
bad = []
for c, want_c in cases.items():
    lcd.command(lcd.ramwr); lcd.x0, lcd.x1, lcd.y0 = 0, 10, 0; lcd.x, lcd.y = 0, 0
    lcd.fb.clear()
    call(syms['pixel_hook'], (c,))
    if lcd.fb.get((0, 0)) != want_c:
        bad.append((hex(c), hex(lcd.fb.get((0, 0)) or 0), hex(want_c)))
check('colour remap: colours -> purple, greys -> dim, black/white/red kept', not bad, bad)
sys.exit(0 if ok else 1)

# Render the patched boot splash (screen.c: splash) from its fill calls and compare it with the
# firmware font scaled 3x. usage: screen_test.py patched.bin patch.elf
import struct
import subprocess
import sys

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, \
    UC_ARM_REG_R3, UC_ARM_REG_SP

BASE, FILL, FONT = 0x08001800, 0x08005740, 0x0800F01A
img = open(sys.argv[1], 'rb').read()
syms = {l.split()[-1]: int(l.split()[0], 16) for l in
        subprocess.check_output(['arm-none-eabi-nm', sys.argv[2]], text=True).splitlines()}
hi, lo = struct.unpack_from('<HH', img, 0x0800DE62 - BASE)
off = ((hi & 0x7FF) << 11 | (lo & 0x7FF)) << 1
assert 0x0800DE62 + 4 + (off - (1 << 23) if off & (1 << 22) else off) == syms['splash'], 'splash not hooked'

uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
uc.mem_map(0x08000000, 0x10000); uc.mem_write(BASE, img); uc.mem_map(0x20000000, 0x2000)
screen = [[' '] * 128 for _ in range(128)]
fills = []

def hk(uc, a, sz, _):
    x0, y0, x1, y1 = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3))
    col = struct.unpack('<I', uc.mem_read(uc.reg_read(UC_ARM_REG_SP), 4))[0] & 0xFFFF
    fills.append(col)
    for y in range(y0, y1):
        for x in range(x0, x1):
            screen[y][x] = '#'
    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
uc.hook_add(UC_HOOK_CODE, hk, begin=FILL, end=FILL)
uc.reg_write(UC_ARM_REG_SP, 0x20001800); uc.reg_write(UC_ARM_REG_LR, 0x08000001)
uc.emu_start(syms['splash'] | 1, 0x08000000, count=500000)

# expected: font rows (bit 0 = left), 3x, from x=10, y=47
want = [[' '] * 128 for _ in range(128)]
for i, ch in enumerate('LCR-P1'):
    g = img[FONT - BASE + (ord(ch) - 32) * 12:][:12]
    for r, row in enumerate(g):
        for b in range(8):
            if row >> b & 1:
                for dy in range(3):
                    for dx in range(3):
                        want[47 + r * 3 + dy][10 + i * 18 + b * 3 + dx] = '#'
rows = [''.join(r).rstrip() for r in screen]
print('\n'.join(r[:120] for r in rows[44:86:2]))
ok = screen == want and set(fills) == {0x5DDF}
print(f"{'PASS' if ok else 'FAIL'}  boot splash: 'LCR-P1' at 3x in accent ({len(fills)} fills)")
sys.exit(0 if ok else 1)

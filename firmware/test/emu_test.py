# Emulate the patched draw paths (draw_string / draw_uint / draw_float / glyph string) with stubbed
# LCD + UART: check the UART text, and that the original routines still get the same arguments.
import sys, struct
from unicorn import *
from unicorn.arm_const import *
img = open(sys.argv[1], 'rb').read()
BASE, PUTC, DRAWCHAR, DRAWGLYPH = 0x08001800, 0x0800B2DC, 0x08005C20, 0x08005D24
DRAWSTR, DRAWUINT, DRAWGSTR = 0x080060C0, 0x08005F50, 0x08005CF0
uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
uc.mem_map(0x08000000, 0x10000); uc.mem_write(BASE, img)
uc.mem_map(0x20000000, 0x2000)
uc.mem_write(0x20001F00, b'\x00' * 4)
out, chars, glyphs = [], [], []
RET = 0x08000001  # sentinel return
def hk(uc, addr, size, _):
    sp = uc.reg_read(UC_ARM_REG_SP)
    r = [uc.reg_read(x) for x in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
    if addr == PUTC:
        out.append(r[0] & 0xff)
    elif addr == DRAWCHAR:
        chars.append((r[0], r[1], chr(r[2]), r[3], struct.unpack('<3I', uc.mem_read(sp, 12))))
    elif addr == DRAWGLYPH:
        glyphs.append((r[0], r[1], bytes(uc.mem_read(r[2], 2)), struct.unpack('<3I', uc.mem_read(sp, 12))))
    else:
        return
    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))  # stub returns
for a in (PUTC, DRAWCHAR, DRAWGLYPH):
    uc.hook_add(UC_HOOK_CODE, hk, begin=a, end=a)
def call(fn, args, stack):
    sp = 0x20001800 - 4 * len(stack)
    uc.mem_write(sp, struct.pack(f'<{len(stack)}I', *stack))
    for reg, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args):
        uc.reg_write(reg, v)
    for reg, v in zip((UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7), (0x44, 0x55, 0x66, 0x77)):
        uc.reg_write(reg, v)
    uc.reg_write(UC_ARM_REG_SP, sp); uc.reg_write(UC_ARM_REG_LR, RET)
    uc.emu_start(fn | 1, RET & ~1, count=200000)
    assert uc.reg_read(UC_ARM_REG_SP) == sp, 'SP not restored'
    assert [uc.reg_read(x) for x in (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7)] == [0x44, 0x55, 0x66, 0x77], 'r4-r7 clobbered'
STR = 0x20001000
uc.mem_write(STR, b'Cap:\x00'); call(DRAWSTR, (5, 0x57, STR, 0xffff), (0, 12, 1))
call(DRAWUINT, (30, 0x57, 4712, 4), (0xffff, 0, 12, 0))
uc.mem_write(STR, b'\xc5\xb7\x00'); call(DRAWGSTR, (60, 0x58, STR, 0xffff), (0, 12, 1))
uc.mem_write(STR, b'Resistance\x00'); call(DRAWSTR, (5, 0x20, STR, 0xffff), (0, 12, 1))
print('UART  :', repr(bytes(out).decode()))
assert bytes(out).decode() == '\nCap: 4712ohm\nResistance', 'mirror output wrong'
assert ''.join(c[2] for c in chars) == 'Cap:4712Resistance' and len(glyphs) == 1, 'draw calls not passed through'
print('chars :', ''.join(c[2] for c in chars), '| first', chars[0], '| uint first', chars[4])
print('glyphs:', glyphs)
FLOAT = 0x08005E10  # draw_float_1(x, y, float, ndigits, [sp] fg, bg, size)
f = struct.unpack('<I', struct.pack('<f', 47.12))[0]
out.clear()
uc.mem_write(STR, b'value:\x00'); call(DRAWSTR, (5, 0x40, STR, 0xffff), (0, 12, 1))
call(FLOAT, (0x2a, 0x40, f, 3), (0xffff, 0, 12))
uc.mem_write(STR, b'k\x00'); call(DRAWSTR, (0x2a + 6 * 4 + 2, 0x40, STR, 0xffff), (0, 12, 1))
print('UART2 :', repr(bytes(out).decode()))
assert bytes(out).decode() == '\nvalue:47.1 k', 'float value mirrored wrongly'
print('PASS  serial mirror: strings, integers, floats, ohm glyph; registers and stack preserved')

#!/usr/bin/env python3
"""Build the patched FNIRSI LCR-P1 firmware from the stock v1.1.0 image.

    python3 firmware/make_patch.py [stock.BIN]     (default: fw/APP_LCR-P1-EN_V1.1.0_260324.BIN)

Output: build/APP_LCR-P1-EN_V1.1.0u.BIN (same size as stock). Options (environment):
    AUTOOFF_S=300    auto power-off after N s idle at power-on (stock 60, allowed 30..3600;
                     changeable over USB with #A<s>)
    LOWBAT_MV=3400   low-battery shutdown, value as shown on the battery screen (stock ~3500)
    BOOT_ART=mask    boot picture, from firmware/art/<name>.py (mask, skull)
    FLASH_DUMP=1     diagnostic build (..._flashdump.BIN): send 'D' over USB serial to dump the bootloader
    PIN_DEBUG=1      diagnostic build (..._pindebug.BIN): logs GPIO changes over USB serial instead of
                     taking serial commands (firmware/pindump.c, PC side tools/pinlog.py)

Every patched location is checked against the stock bytes first; anything unexpected stops
the build. Needs arm-none-eabi-gcc / binutils.
"""
import hashlib
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, 'build')
STOCK = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'fw', 'APP_LCR-P1-EN_V1.1.0_260324.BIN')
STOCK_SHA256 = '5ccce71cec5798e9a88befe5b29613f2c38cd3f10fca85a0e64c5bed4ded85a5'
OUT = os.path.join(BUILD, 'APP_LCR-P1-EN_V1.1.0u.BIN')
if os.environ.get('PIN_DEBUG') == '1':
    OUT = OUT.replace('.BIN', '_pindebug.BIN')
if os.environ.get('FLASH_DUMP') == '1':
    OUT = OUT.replace('.BIN', '_flashdump.BIN')

BASE = 0x08001800                                   # app link address (bootloader below)
DRAWCHAR, DRAWGLYPH = 0x08005C20, 0x08005D24
CHAR_SITES = [0x8005c08, 0x8005e6e, 0x8005e9a, 0x8005f12, 0x8005f3c, 0x8005fc6, 0x80060de]
GLYPH_SITES = [0x8005bf0, 0x8005d10]
GLYPHS, GLYPH_N, GLYPH_SZ = 0x0800F48E, 58, 26      # GBK glyph table; EN firmware uses entry 33 only
FREE_END = GLYPHS + 33 * GLYPH_SZ
FONT = 0x0800F01A                                   # 6x12 ASCII font, 12 bytes per char from ' '
# characters no string in the firmware (nor any patch label) ever draws; their glyphs hold code
NEVER_DRAWN = set('"#$&\'*,;<=>?@QXYZ[\\]^`qz{|}~')
KEY_SITE, KEYSCAN = 0x0800B964, 0x080051E0          # main loop: BL key-queue read
IR_SITE, IR_STOCK = 0x0800BBAA, 0x08004848          # main loop: BL stock NEC decoder


def bl(src, dst):
    off = (dst - (src + 4)) >> 1
    assert -(1 << 21) <= off < (1 << 21)
    return struct.pack('<HH', 0xF000 | ((off >> 11) & 0x7FF), 0xF800 | (off & 0x7FF))


def run(*a):
    subprocess.check_call(list(a), cwd=BUILD)


def sym(elf, name):
    for line in subprocess.check_output(['arm-none-eabi-nm', elf], text=True).splitlines():
        a, _, n = line.split()
        if n == name:
            return int(a, 16)
    raise KeyError(name)


REGION_AT = {'.patchE': 0x0800F2BC, '.patchF': 0x0800F160}   # used when a region is empty (no section)


def section_addr(elf, name):
    for line in subprocess.check_output(['arm-none-eabi-objdump', '-h', elf], text=True).splitlines():
        f = line.split()
        if len(f) > 3 and f[1] == name:
            return int(f[3], 16)
    if name in REGION_AT:
        return REGION_AT[name]
    raise KeyError(name)


if not os.path.exists(STOCK):
    sys.exit(f'Stock firmware not found: {STOCK}\nSee fw/README.md.')
img = bytearray(open(STOCK, 'rb').read())
stock = bytes(img)
if hashlib.sha256(stock).hexdigest() != STOCK_SHA256:
    sys.exit('This is not the stock LCR-P1 v1.1.0 image (APP_LCR-P1-EN_V1.1.0_260324.BIN); '
             'the patch only applies to that exact file.')

os.makedirs(BUILD, exist_ok=True)
src = lambda f: os.path.join(HERE, f)                # noqa: E731
run('arm-none-eabi-as', '-o', 'uart_mirror.o', src('uart_mirror.S'))
run('arm-none-eabi-gcc', '-Os', '-mcpu=cortex-m0', '-mthumb', '-ffreestanding', '-fno-builtin', '-nostdlib',
    '-fno-common', '-ffunction-sections', '-fdata-sections', '-Wall', '-Werror', '-c', src('ir_decode.c'),
    '-o', 'ir_decode.o')
AUTOOFF_S = int(os.environ.get('AUTOOFF_S', '300'))
assert 30 <= AUTOOFF_S <= 3600, 'AUTOOFF_S: 30..3600'
FLASH_DUMP = os.environ.get('FLASH_DUMP') == '1'     # bootloader dump over serial (firmware/flashdump.c)
PIN_DEBUG = os.environ.get('PIN_DEBUG') == '1' or FLASH_DUMP   # diagnostic builds: no commands, no IR patch
CTL = 'flashdump' if FLASH_DUMP else 'pindump' if PIN_DEBUG else 'control'
run('arm-none-eabi-gcc', '-Os', '-mcpu=cortex-m0', '-mthumb', '-ffreestanding', '-fno-builtin', '-nostdlib',
    *(['-fno-tree-loop-distribute-patterns'] if PIN_DEBUG else []), '-fno-common', '-ffunction-sections',
    '-fdata-sections', '-Wall', '-Werror', f'-DAUTOOFF_DEFAULT_S={AUTOOFF_S}', '-c', src(CTL + '.c'), '-o', CTL + '.o')
run('arm-none-eabi-gcc', '-Os', '-mcpu=cortex-m0', '-mthumb', '-ffreestanding', '-fno-builtin',
    '-fno-tree-loop-distribute-patterns', '-nostdlib', '-fno-common', '-ffunction-sections', '-fdata-sections',
    '-Wall', '-Werror', '-c', src('screen.c'), '-o', 'screen.o')
if PIN_DEBUG:   # link.ld names these files: empty stand-ins (no serial commands, no IR patch)
    for f in ('control', 'ir_decode'):
        run('arm-none-eabi-gcc', '-mcpu=cortex-m0', '-mthumb', '-x', 'c', '-c', '/dev/null', '-o', f + '.o')
run('arm-none-eabi-ld', '-T', src('link.ld'), '-e', 'hook_char', '--orphan-handling=error', '-o', 'patch.elf',
    'uart_mirror.o', 'ir_decode.o', 'control.o', 'screen.o', *([CTL + '.o'] if PIN_DEBUG else []))
elf = os.path.join(BUILD, 'patch.elf')
code = {}
for r in 'ABCDEFG':
    run('arm-none-eabi-objcopy', '-O', 'binary', '-j', f'.patch{r}', 'patch.elf', f'patch{r}.bin')
    code[r] = open(os.path.join(BUILD, f'patch{r}.bin'), 'rb').read()
CODE_AT, CODEB_AT = section_addr(elf, '.patchA'), section_addr(elf, '.patchB')
HOOK_GLYPH = sym(elf, 'hook_glyph')
o = lambda a: a - BASE                                # noqa: E731

assert CODE_AT + len(code['A']) <= FREE_END, 'region A does not fit'


def font_chars(lo, hi):
    return {chr(0x20 + (a - FONT) // 12) for a in range(lo, hi)}


for r, lo, hi in (('A', CODE_AT, GLYPHS), ('E', section_addr(elf, '.patchE'), None),
                  ('F', section_addr(elf, '.patchF'), None)):
    hi = hi or lo + len(code[r])
    assert FONT <= lo and hi <= GLYPHS and font_chars(lo, hi) <= NEVER_DRAWN, f'region {r} hits a used glyph'
B_START, B_END = GLYPHS + 34 * GLYPH_SZ + 2, GLYPHS + GLYPH_N * GLYPH_SZ
assert CODEB_AT >= B_START and CODEB_AT + len(code['B']) <= B_END, 'region B does not fit'

# Low-battery shutdown: main loop compares 2*ADC(battery divider) <= 3300 mV (literal at
# 0x0800BCE0, single use at 0x0800B986). The battery screen shows that reading + 200 mV.
LOWBAT_LIT = 0x0800BCE0
LOWBAT_MV = int(os.environ.get('LOWBAT_MV', '3400'))
assert 3300 <= LOWBAT_MV <= 3600, 'LOWBAT_MV: keep 3300..3600 (3.3 V regulator headroom)'
assert struct.unpack_from('<I', img, o(LOWBAT_LIT))[0] == 3300
struct.pack_into('<I', img, o(LOWBAT_LIT), LOWBAT_MV - 200)

# Auto power-off: the 200 Hz timer ISR (IRQ20, 0x080060F4) counts idle ticks at 0x200006F0
# (cleared on activity) and powers off at 12000 ticks = 60 s (literal 0x0800615C, single use).
# The patch's main-loop hook (control.c) now applies the limit (AUTOOFF_S at power-on, changeable
# over USB); the ISR keeps a 60 min hard limit for screens where the main loop does not run.
AUTOOFF_LIT = 0x0800615C
assert struct.unpack_from('<I', img, o(AUTOOFF_LIT))[0] == 12000
struct.pack_into('<I', img, o(AUTOOFF_LIT), 3600 * 200)

# Serial commands: enable USART1 receive (RX = PA10, unused by the firmware) and hook the main
# loop's key-queue read (control.c: key_hook).
for a, old_b, new_b in ((0x08008F5C, b'\x01\x21', b'\x03\x21'),   # movs r1,#1 -> #3: pins 0x200 -> 0x600 (PA9+PA10)
                        (0x08008F80, b'\x08\x20', b'\x0c\x20')):  # movs r0,#8 -> #12: USART mode TX -> TX|RX
    assert img[o(a):o(a) + 2] == old_b, hex(a)
    img[o(a):o(a) + 2] = new_b
assert img[o(KEY_SITE):o(KEY_SITE) + 4] == bl(KEY_SITE, KEYSCAN)
img[o(KEY_SITE):o(KEY_SITE) + 4] = bl(KEY_SITE, sym(elf, 'key_hook'))
if PIN_DEBUG and not FLASH_DUMP:   # GPIO sampler wraps the stock 200 Hz timer ISR (IRQ20)
    VEC_IRQ20, STOCK_TICK_ISR = 0x90, 0x080060F5
    assert struct.unpack_from('<I', img, VEC_IRQ20)[0] == STOCK_TICK_ISR
    struct.pack_into('<I', img, VEC_IRQ20, sym(elf, 'pin_isr') | 1)

# Boot screen: the FNIRSI logo (1-bit bitmap, 93x18, 222 bytes at 0x0800EECA; only used by the
# splash routine 0x0800DE50) is overwritten with a 32x32 picture (art/<BOOT_ART>.py), drawn by the
# stock code in the accent colour and centred. The version line loses its "--- V:" / " ---"
# decoration and is centred in grey. The rest of the old bitmap becomes region G.
sys.path.insert(0, os.path.join(HERE, 'art'))
art = __import__(os.environ.get('BOOT_ART', 'mask')).bitmap()
LOGO, LOGO_LEN = 0x0800EECA, 222
assert hashlib.sha256(stock[o(LOGO):o(LOGO) + LOGO_LEN]).hexdigest()[:16] == 'f4772a4a91733ff4', 'logo not stock'
assert struct.unpack_from('<I', img, o(0x0800DEBC))[0] == LOGO and len(art) <= 0x0800EF50 - LOGO
img[o(LOGO):o(LOGO) + len(art)] = art
W, H = art[2], art[4]
for a, old_b, new_b in (
        (0x0800DE5A, b'\x38\x22', bytes([40, 0x22])),                  # movs r2,#56 -> y 40
        (0x0800DE5C, b'\x13\x21', bytes([(128 - W) // 2, 0x21])),      # movs r1,#19 -> x centred
        (0x0800DE84, b'\x37\x20', bytes([(128 - 5 * 6) // 2, 0x20])),  # "1.1.0" x 55 -> centred
        (0x0800DEC4, b'-', b'\x00'),                                     # "--- V:" -> ""
        (0x0800DED4, b' ', b'\x00')):                                    # " ---"   -> ""
    assert img[o(a):o(a) + len(old_b)] == old_b, hex(a)
    img[o(a):o(a) + len(new_b)] = new_b
for a, old_v, new_v in ((0x0800DEB8, 0x04DB, 0xB47F),                     # picture colour -> accent
                        (0x0800DEC0, 0xFFFF, 0x8C33)):                    # version text -> label grey
    assert struct.unpack_from('<I', img, o(a))[0] == old_v, hex(a)
    struct.pack_into('<I', img, o(a), new_v)
G_AT = section_addr(elf, '.patchG')
assert G_AT >= LOGO + len(art) and G_AT + len(code['G']) <= LOGO + LOGO_LEN
img[o(G_AT):o(G_AT) + len(code['G'])] = code['G']

# Pin legend on the idle screen (0x0800DEDC): drop the light-blue rounded panel (BL 0x08003BBC at
# 0x0800DF0C), so its black cells and dots vanish, and draw the pin digits grey: a plain matrix.
PANEL_SITE, ROUNDRECT = 0x0800DF0C, 0x08003BBC
assert img[o(PANEL_SITE):o(PANEL_SITE) + 4] == bl(PANEL_SITE, ROUNDRECT)
img[o(PANEL_SITE):o(PANEL_SITE) + 4] = b'\xc0\x46\xc0\x46'          # nop; nop
assert struct.unpack_from('<I', img, o(0x0800E110))[0] == 0xFFFF       # legend digit colour
struct.pack_into('<I', img, o(0x0800E110), 0x8C33)

# Pixel colours go through screen.c: pixel_hook (colour remap). 0x08006060 writes any 16-bit word to
# the LCD: of its 12 callers only these 5 send pixel colours (bitmap x2, line plot, draw_char, glyph);
# the other 7 send window / cursor coordinates (0x08005B48, 0x08005B7C, 0x08006028) and must not be
# touched (remapping those scrambled the whole screen on 2026-11-13).
LCD_WORD = 0x08006060
PIXEL_SITES = [0x080056D2, 0x0800570C, 0x0800573A, 0x08005CBC, 0x08005DCE]
COORD_SITES = [0x08005B60, 0x08005B6C, 0x08005BA2, 0x08005BA8, 0x08005BB4, 0x08005BBA, 0x08006032]
all_sites = [a for a in range(BASE, BASE + len(img) - 4, 2) if img[o(a):o(a) + 4] == bl(a, LCD_WORD)]
assert sorted(all_sites) == sorted(PIXEL_SITES + COORD_SITES), [hex(a) for a in all_sites]
for a in PIXEL_SITES:
    img[o(a):o(a) + 4] = bl(a, sym(elf, 'pixel_hook'))

# Serial mirror: every draw_char / glyph call goes through the hooks in uart_mirror.S.
for s in CHAR_SITES:
    assert img[o(s):o(s) + 4] == bl(s, DRAWCHAR), hex(s)
    img[o(s):o(s) + 4] = bl(s, CODE_AT)
for s in GLYPH_SITES:
    assert img[o(s):o(s) + 4] == bl(s, DRAWGLYPH), hex(s)
    img[o(s):o(s) + 4] = bl(s, HOOK_GLYPH)
# NOTE: the firmware's "=== Check Pin ===" debug prints (0x08002458, 0x0800246C) must stay:
# the ~4 ms each takes on the UART is settling time in the pin probe. Without them a 1 Mohm
# resistor was measured as 2 pF.
img[o(CODE_AT):o(CODE_AT) + len(code['A'])] = code['A']
for r in 'BEF':
    a = section_addr(elf, f'.patch{r}')
    img[o(a):o(a) + len(code[r])] = code[r]

if not PIN_DEBUG:   # IR patch (left out of the PIN_DEBUG build for code space: stock NEC decoder)
    IR_POLL = sym(elf, 'ir_poll')
    assert img[o(IR_SITE):o(IR_SITE) + 4] == bl(IR_SITE, IR_STOCK)
    img[o(IR_SITE):o(IR_SITE) + 4] = bl(IR_SITE, IR_POLL)
    # our IR edge ISR on vector IRQ8 (EXTI11)
    VEC_IRQ8, STOCK_EDGE_ISR = 0x60, 0x08003C4D
    assert struct.unpack_from('<I', img, VEC_IRQ8)[0] == STOCK_EDGE_ISR
    struct.pack_into('<I', img, VEC_IRQ8, sym(elf, 'ir_edge_isr') | 1)
    # regions C/D: stock routines whose only callers were redirected above (dead code now)
    for r, lo, hi, h in (('C', 0x08004848, 0x08004978, '2e1630909f0e6a95'),
                         ('D', 0x08003C4C, 0x08003CB0, 'bea7575f7a8bc84e')):
        assert hashlib.sha256(stock[o(lo):o(hi)]).hexdigest()[:16] == h, f'region {r} not stock'
        assert section_addr(elf, f'.patch{r}') == lo and len(code[r]) <= hi - lo, r
        img[o(lo):o(lo) + len(code[r])] = code[r]
    # IR capture: frame-end timeout 15 ms -> 50 ms (keeps AC remotes' gaps in one capture)
    ARR_LIT = 0x08004A24
    assert struct.unpack_from('<I', img, o(ARR_LIT))[0] == 15000
    struct.pack_into('<I', img, o(ARR_LIT), 50000)

# The glyph lookup scans all 58 keys; only "ohm" (c5 b7, entry 33) may match, and must be intact.
keys = [bytes(img[o(GLYPHS) + i * GLYPH_SZ:o(GLYPHS) + i * GLYPH_SZ + 2]) for i in range(GLYPH_N)]
assert [i for i, k in enumerate(keys) if k == b'\xc5\xb7'] == [33], 'glyph key collision'
e33 = o(GLYPHS) + 33 * GLYPH_SZ
assert img[e33:e33 + GLYPH_SZ] == stock[e33:e33 + GLYPH_SZ], 'ohm glyph modified'
assert len(img) == len(stock)

open(OUT, 'wb').write(img)
print(f'wrote {os.path.relpath(OUT, ROOT)}  sha256 {hashlib.sha256(img).hexdigest()[:16]}')
print(f'  auto power-off {AUTOOFF_S} s at power-on (60 min hard limit), low-battery cutoff {LOWBAT_MV} mV')
print(f'  code: A {len(code["A"])}/{FREE_END - CODE_AT}  B {len(code["B"])}/{B_END - CODEB_AT}  '
      f'C {len(code["C"])}/304  D {len(code["D"])}/100  E {len(code["E"])}/82  F {len(code["F"])}/70  '
      f'G {len(code["G"])}/88 bytes')

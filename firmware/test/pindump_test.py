# Drive the PIN_DEBUG build (firmware/pindump.c): timer-ISR GPIO sampler + main-loop printer.
# usage: pindump_test.py patched_pindebug.bin patch.elf
import re
import struct
import subprocess
import sys

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, \
    UC_ARM_REG_R3, UC_ARM_REG_SP

BASE, KEYSCAN, PRINTF, STOCK_ISR = 0x08001800, 0x080051E0, 0x08008FA4, 0x080060F4
TICKS = 0x200006F4
PID = {'A': 0x40010810, 'B': 0x40010C10, 'C': 0x40011010, 'F': 0x40011C10}
POD = {k: v + 4 for k, v in PID.items()}
img = open(sys.argv[1], 'rb').read()
syms = {l.split()[-1]: int(l.split()[0], 16) for l in
        subprocess.check_output(['arm-none-eabi-nm', sys.argv[2]], text=True).splitlines()}
fails = []


def check(name, good, info=''):
    print(('PASS ' if good else 'FAIL ') + name + ('' if good else f'  {info}'))
    if not good:
        fails.append(name)


def bl_target(src):
    hi, lo = struct.unpack_from('<HH', img, src - BASE)
    off = ((hi & 0x7FF) << 11 | (lo & 0x7FF)) << 1
    return src + 4 + (off - (1 << 23) if off & (1 << 22) else off)


check('main loop calls key_hook', bl_target(0x0800B964) == syms['key_hook'])
check('IRQ20 vector -> pin_isr', struct.unpack_from('<I', img, 0x90)[0] == syms['pin_isr'] | 1)
check('IR patch left out (stock NEC call)', bl_target(0x0800BBAA) == 0x08004848)
check('IRQ8 vector stock', struct.unpack_from('<I', img, 0x60)[0] == 0x08003C4D)

uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
uc.mem_map(0x08000000, 0x10000); uc.mem_write(BASE, img)
uc.mem_map(0x20000000, 0x4000); uc.mem_map(0x40010000, 0x2000)
uc.mem_write(0x20002000, b'\xA5' * 0x200)           # power-on garbage
out, stock_calls = [], [0]


def fmt(f, args):
    def rep(m):
        v = args.pop(0)
        return chr(v) if m.group(1) == 'c' else (f'{v:x}' if m.group(1) == 'x' else str(v))
    return re.sub(r'%([cdx])', rep, f)


def on_code(uc, a, sz, _):
    if a == KEYSCAN:
        uc.reg_write(UC_ARM_REG_R0, 0)
    elif a == STOCK_ISR:
        stock_calls[0] += 1
        t = struct.unpack('<I', uc.mem_read(TICKS, 4))[0]
        uc.mem_write(TICKS, struct.pack('<I', t + 1))
    elif a == PRINTF:
        r = [uc.reg_read(x) for x in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        sp = uc.reg_read(UC_ARM_REG_SP)
        args = r[1:] + list(struct.unpack('<8I', uc.mem_read(sp, 32)))
        out.append(fmt(bytes(uc.mem_read(r[0], 64)).split(b'\0')[0].decode(), args))
    else:
        return
    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))


for a in (KEYSCAN, PRINTF, STOCK_ISR):
    uc.hook_add(UC_HOOK_CODE, on_code, begin=a, end=a)
RET = 0x08000100
uc.mem_write(RET, b'\xfe\xe7')


def call(fn):
    uc.reg_write(UC_ARM_REG_LR, RET | 1); uc.reg_write(UC_ARM_REG_SP, 0x20003F00)
    uc.emu_start(fn | 1, RET, count=200000)


def setreg(a, v):
    uc.mem_write(a, struct.pack('<I', v))


def text():
    s = ''.join(out); out.clear()
    return [l for l in s.split('\n') if l]


isr, hook = syms['pin_isr'], syms['key_hook']
call(isr)
check('ISR inactive before init, still chains to stock ISR', stock_calls[0] == 1 and not out)
call(hook)                                            # init (RAM garbage -> zeroed)
check('no snapshot before a second has passed', not text())

setreg(POD['A'], 0x0102)                              # PA8 high (power latch) + PA1 (probe, excluded)
setreg(PID['C'], 0x6000)                              # PC13/PC14 pulled up
setreg(PID['A'], 0x0E00)                              # UART/IR pins high: excluded
call(isr)
call(hook)
ev = [l for l in text() if l.startswith('#E')]
check('initial state reported as events (excluded-only regs silent)',
      ev == ['#E 1 2 6000 6000', '#E 1 4 102 102'], ev)

setreg(POD['F'], 0x40); call(isr)                     # PF6 on
setreg(POD['B'], 0x10); call(isr)                     # PB4 toggles every tick: busy
for i in range(6):
    setreg(POD['B'], 0x10 if i % 2 else 0); call(isr)
call(hook)
ev = [l for l in text() if l.startswith('#E')]
check('PF6 event, PB4 reported once then suppressed', ev == ['#E 2 7 40 40', '#E 3 5 10 10'], ev)

for _ in range(200):                                  # one second with PB4 toggling
    v = struct.unpack('<I', uc.mem_read(POD['B'], 4))[0] ^ 0x10
    setreg(POD['B'], v); call(isr)
call(hook)
lines = text()
s = [l for l in lines if l.startswith('#S')]
b = [l for l in lines if l.startswith('#B')]
check('no events for a busy pin', not [l for l in lines if l.startswith('#E')], lines)
check('snapshot has 13 fields', len(s) == 1 and len(s[0].split()) == 14, s)
check('busy line marks PB4 POD', len(b) == 1 and b[0].split()[1:] == ['0', '0', '0', '0', '0', '10', '0', '0'], b)

setreg(POD['B'], 0)                                   # stop toggling; busy clears after 2 s
for _ in range(400):
    call(isr)
call(hook); text()
setreg(POD['B'], 0x10); call(isr); call(hook)
ev = [l for l in text() if l.startswith('#E')]
check('busy pin reported again once quiet', len(ev) == 1 and ev[0].split()[2:] == ['5', '10', '10'], ev)

for r in (POD['C'], POD['F'], PID['F']):              # quiet start, busy masks cleared
    setreg(r, 0)
for _ in range(400):
    call(isr)
call(hook); text()
regs, vals = (POD['C'], POD['F'], PID['F']), [0, 0, 0]
for i in range(40):                                   # 40 fresh bits without the main loop: ring holds 31
    vals[i // 16] |= 1 << (i % 16)
    setreg(regs[i // 16], vals[i // 16]); call(isr)
call(hook)
lines = text()
ev = [l for l in lines if l.startswith('#E')]
check('ring buffer holds 31 events', len(ev) == 31, len(ev))
for _ in range(200):
    call(isr)
call(hook)
s = [l for l in text() if l.startswith('#S')]
check('9 dropped events counted', s and int(s[-1].split()[-1]) == 9, s)

print(f'{len(fails)} failed' if fails else 'all passed')
sys.exit(1 if fails else 0)

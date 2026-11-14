# Drive the patched main-loop hook (control.c: key_hook) with emulated USART1 / key queue / ticks.
# usage: control_test.py patched.bin patch.elf
import struct
import subprocess
import sys

from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
from unicorn.arm_const import (UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                               UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                               UC_ARM_REG_SP)

BASE, KEYSCAN, PRINTF = 0x08001800, 0x080051E0, 0x08008FA4
U_STS, U_DAT = 0x40013800, 0x40013804
TICKS, IDLE, PWROFF, IRF = 0x200006F4, 0x200006F0, 0x200006ED, 0x20000802
img = open(sys.argv[1], 'rb').read()
syms = {l.split()[-1]: int(l.split()[0], 16) for l in
        subprocess.check_output(['arm-none-eabi-nm', sys.argv[2]], text=True).splitlines()}
HOOK = syms['key_hook']

# the main loop must call the hook, and USART1 must have RX enabled on PA9+PA10
def bl_target(src):
    hi, lo = struct.unpack_from('<HH', img, src - BASE)
    off = ((hi & 0x7FF) << 11 | (lo & 0x7FF)) << 1
    if off & (1 << 22):
        off -= 1 << 23
    return src + 4 + off
assert bl_target(0x0800B964) == HOOK, 'main loop not hooked'
assert img[0x08008F5C - BASE:0x08008F5E - BASE] == b'\x03\x21' and img[0x08008F80 - BASE:0x08008F82 - BASE] == b'\x0c\x20'

uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
uc.mem_map(0x08000000, 0x10000); uc.mem_write(BASE, img)
uc.mem_map(0x20000000, 0x2000); uc.mem_map(0x40000000, 0x20000)
uc.mem_write(0x20001F04, bytes(range(7, 7 + 28)))           # power-on garbage
rx, out, key = [], [], [0]

def on_code(uc, a, sz, _):
    if a == KEYSCAN:
        uc.reg_write(UC_ARM_REG_R0, key[0]); key[0] = 0
    elif a == PRINTF:
        r = [uc.reg_read(x) for x in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]
        sp = uc.reg_read(UC_ARM_REG_SP)
        args = r[1:] + list(struct.unpack('<I', uc.mem_read(sp, 4)))
        fmt = bytes(uc.mem_read(r[0], 40)).split(b'\0')[0].decode()
        for spec in ('%c', '%d', '%d', '%d'):
            v = args.pop(0)
            fmt = fmt.replace(spec, chr(v) if spec == '%c' else str(v), 1)
        out.append(fmt)
    else:
        return
    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
for a in (KEYSCAN, PRINTF):
    uc.hook_add(UC_HOOK_CODE, on_code, begin=a, end=a)

def on_read(uc, access, a, size, value, _):
    if a == U_STS:
        uc.mem_write(U_STS, struct.pack('<H', 0x20 if rx else 0xC0))
    elif a == U_DAT:
        uc.mem_write(U_DAT, struct.pack('<H', rx.pop(0) if rx else 0))
uc.hook_add(UC_HOOK_MEM_READ, on_read, begin=U_STS, end=U_DAT + 1)

def setw(a, v, n=4):
    uc.mem_write(a, v.to_bytes(n, 'little'))

def call(send=b'', k=0, ticks=None, idle=None, ir=False):
    rx.extend(send); key[0] = k; out.clear()
    if ticks is not None: setw(TICKS, ticks)
    if idle is not None: setw(IDLE, idle)
    setw(IRF, 4 if ir else 1, 1)
    for reg, v in zip((UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7), (0x44, 0x55, 0x66, 0x77)):
        uc.reg_write(reg, v)
    uc.reg_write(UC_ARM_REG_SP, 0x20001800); uc.reg_write(UC_ARM_REG_LR, 0x08000001)
    uc.emu_start(HOOK | 1, 0x08000000, count=20000)
    assert uc.reg_read(UC_ARM_REG_SP) == 0x20001800
    assert [uc.reg_read(x) for x in (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7)] == [0x44, 0x55, 0x66, 0x77]
    return uc.reg_read(UC_ARM_REG_R0) & 0xFF, ''.join(out), uc.mem_read(PWROFF, 1)[0]

ok = True
def check(name, cond, info=''):
    global ok
    ok &= bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {info}")

setw(PWROFF, 0, 1)
r = call(ticks=1000, idle=0);            check('power-on: garbage settings reset, nothing injected', r == (0, '', 0), r)
r = call(b'#?\n');                         check('#? status, default auto-off', r[1] == '\n#? R=0 A=300 P=0\n', repr(r[1]))
r = call(k=0x2B);                          check('real key passes through', r[0] == 0x2B, r)
r = call(b'#T\n');                         check('#T -> TEST key 0x21', r[0] == 0x21 and r[1] == '\n#T R=0 A=300 P=0\n', r)
r = call(b'#T');                           check('no newline yet -> nothing', r[0] == 0 and r[1] == '', r)
r = call(b'\r');                           check('...CR completes it', r[0] == 0x21, r)
r = call(b'#R2\n', ticks=2000);            check('#R2 set', r[1] == '\n#R R=2 A=300 P=0\n', repr(r[1]))
r = call(ticks=2399);                      check('repeat: not before 2 s', r[0] == 0, r)
r = call(ticks=2400);                      check('repeat: test injected at 2 s', r[0] == 0x21, r)
r = call(ticks=2450);                      check('test finished (next loop): wait restarts', r[0] == 0, r)
r = call(ticks=2849);                      check('repeat: 2 s after the test finished, not after injection', r[0] == 0, r)
r = call(ticks=2850);                      check('repeat: next test', r[0] == 0x21, r)
call(ticks=2851)
r = call(ticks=3300, ir=True);             check('repeat: never on the IR page', r[0] == 0, r)
r = call(k=0x2B, ticks=3400);              check('real key restarts the wait', r[0] == 0x2B, r)
r = call(ticks=3799);                      check('...not before 2 s after the key', r[0] == 0, r)
r = call(b'#R0\n', ticks=9000);            check('#R0 stops repeating', r[0] == 0 and 'R=0' in r[1], r)
r = call(b'#A1\n', idle=199);              check('#A1, idle 199 ticks -> stay on', r[2] == 0 and 'A=1' in r[1], r)
r = call(idle=200);                        check('idle 200 ticks (1 s) -> power off', r[2] == 1, r)
setw(PWROFF, 0, 1)
r = call(b'#A0\n', idle=10**6);            check('#A0 -> never from the hook', r[2] == 0, r)
r = call(b'#O\n');                         check('#O -> power off now', r[2] == 1 and r[1].startswith('\n#O'), r)
setw(PWROFF, 0, 1)
r = call(b'#R99999\n');                    check('values clamped to 3600', 'R=3600' in r[1], repr(r[1]))
r = call(b'#Z\n');                         check('unknown command -> #!', r[1].startswith('\n#! '), repr(r[1]))
r = call(b'#R1x\n');                       check('malformed line dropped', r[1] == '' and r[0] == 0, r)
r = call(b'#R123456789012\n');             check('over-long line dropped', r[1] == '', r)
r = call(b'\x00\xff garbage T\n');         check('line noise without # ignored', r == (0, '', 0), r)
r = call(b'#R0\n#A300\n#T\n');             check('several commands in one read', r[0] == 0x21 and r[1].count('#') == 3, repr(r[1]))
sys.exit(0 if ok else 1)

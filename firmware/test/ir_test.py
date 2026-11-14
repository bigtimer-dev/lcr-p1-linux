# Feed synthetic IR captures through the patched edge ISR + ir_poll() (and the stock NEC
# decoder for reference). usage: ir_test.py stock.bin patched.bin patch.elf
import sys, struct, subprocess
from unicorn import *
from unicorn.arm_const import *
BASE = 0x08001800
SHOW, DRAWSTR, PRINTF, STOCK_NEC, FILL = 0x0800D460, 0x080060C0, 0x08008FA4, 0x08004848, 0x08005740
TIM, EXTI = 0x40000800, 0x40010400
MK, SP = +100, -100                    # receiver skew: marks long, spaces short

stock, pat = open(sys.argv[1], 'rb').read(), open(sys.argv[2], 'rb').read()
syms = {l.split()[-1]: int(l.split()[0], 16) for l in
        subprocess.check_output(['arm-none-eabi-nm', sys.argv[3]], text=True).splitlines()}
ISR, POLL = syms['ir_edge_isr'], syms['ir_poll']
assert struct.unpack_from('<I', pat, 0x60)[0] == ISR | 1, 'IRQ8 vector not patched'
assert struct.unpack_from('<I', pat, 0x08004A24 - BASE)[0] == 50000, 'timer period not patched'

def mk(img):
    uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    uc.mem_map(0x08000000, 0x10000); uc.mem_write(BASE, img)
    uc.mem_map(0x20000000, 0x2000); uc.mem_map(0x40000000, 0x20000)
    uc.log, uc.out = [], []
    def hk(uc, a, sz, _):
        r = [uc.reg_read(x) for x in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2)]
        if a == SHOW:
            uc.log.append(('show', bytes(uc.mem_read(0x20000890, 4)).hex()))
        elif a == DRAWSTR:
            uc.log.append(('label', bytes(uc.mem_read(r[2], 24)).split(b'\0')[0].decode().strip()))
        elif a == FILL:
            sp = uc.reg_read(UC_ARM_REG_SP)
            uc.log.append(('fill', tuple(r) + (uc.reg_read(UC_ARM_REG_R3), struct.unpack('<I', uc.mem_read(sp, 4))[0])))
        elif a == PRINTF:
            fmt = bytes(uc.mem_read(r[0], 16)).split(b'\0')[0].decode()
            uc.out.append(fmt.replace('%d', str(r[1]), 1).replace('%02X', f'{r[1]:02X}', 1))
        else:
            return
        uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
    for a in (SHOW, DRAWSTR, PRINTF, FILL):
        uc.hook_add(UC_HOOK_CODE, hk, begin=a, end=a)
    return uc

def call(uc, fn):
    uc.reg_write(UC_ARM_REG_SP, 0x20001800); uc.reg_write(UC_ARM_REG_LR, 0x08000001)
    uc.emu_start(fn | 1, 0x08000000, count=200000)

def capture(uc, durs):
    """Arm, then deliver one EXTI11 interrupt per edge with TIM CNT = time since last edge."""
    uc.mem_write(0x20000802, bytes([0x05, 0])); uc.mem_write(0x20001EF8, b'\0\0' + struct.pack('<H', 0x1A5C))
    for d in durs:
        uc.mem_write(TIM + 0x24, struct.pack('<H', min(d, 0xFFFF)))
        uc.mem_write(EXTI + 0x00, struct.pack('<I', 1 << 11))   # IMASK
        uc.mem_write(EXTI + 0x14, struct.pack('<I', 1 << 11))   # PEND
        armed = uc.mem_read(0x20000802, 1)[0] & 1
        call(uc, ISR)
        if armed:
            assert struct.unpack('<H', uc.mem_read(TIM + 0x24, 2))[0] == 0, 'CNT not reset'
    f = uc.mem_read(0x20000802, 1)[0]
    uc.mem_write(0x20000802, bytes([f & ~1]))                     # 50 ms overflow ISR: frame done

def run_patched(uc, durs):
    uc.log.clear(); uc.out.clear(); uc.mem_write(0x20000890, b'\0' * 4)
    capture(uc, durs)
    call(uc, POLL)
    st = bytes(uc.mem_read(0x20000802, 1)) + bytes(uc.mem_read(0x20001EF8, 2))
    assert st[0] & 1 and st[1:] == b'\0\0', f'not re-armed: {st.hex()}'
    return uc.log

def run_stock_nec(uc, durs):
    uc.log.clear()
    uc.mem_write(0x20000802, bytes([0x04, len(durs)]) + struct.pack(f'<{len(durs)}H', *durs))
    call(uc, STOCK_NEC)
    return dict(uc.log).get('show')

def skew(ms, first_mark=True):
    return [d + (MK if (i % 2 == 0) == first_mark else SP) for i, d in enumerate(ms)]

def pdist_bits(bits):
    out = []
    for b in bits:
        out += [560, 1690 if b else 560]
    return out

def le_bits(bytes_):
    return [b >> k & 1 for b in bytes_ for k in range(8)]

def pdist(lead, bytes4):
    return [0] + skew([lead, 4500] + pdist_bits(le_bits(bytes4)) + [560])

def sony(cmd, addr, nb):
    v = cmd | addr << 7
    out = [2400]
    for k in range(nb):
        out += [600, 1200 if v >> k & 1 else 600]
    return [0] + skew(out)

def rc5(toggle, addr, cmd):
    bits = [1, 1 if cmd < 64 else 0, toggle] + [addr >> i & 1 for i in range(4, -1, -1)] + [cmd >> i & 1 for i in range(5, -1, -1)]
    halves = []
    for b in bits:
        halves += [0, 1] if b else [1, 0]
    halves = halves[1:]
    while halves and halves[-1] == 0:
        halves.pop()
    durs, cur, n = [], halves[0], 0
    for h in halves:
        if h == cur: n += 1
        else: durs.append((cur, n)); cur, n = h, 1
    durs.append((cur, n))
    return [0] + [n * 889 + (MK if lvl else SP) for lvl, n in durs]

def gree_sum(b):
    return (10 + sum(x & 15 for x in b[:4]) + sum(x >> 4 for x in b[4:7])) & 15

def gree_label(b):
    if not b[0] & 8:
        return 'AC off'
    mode = b[0] & 7
    return f"{(b[1] & 15) + 16}C " + (['Auto', 'Cool', 'Dry', 'Fan', 'Heat'][mode] if mode < 5 else f'M{mode}')

def gree(b8, fix_sum=True):
    """Gree-style AC: 9000/4500, 35 bits (4 bytes + 010), 560 + 20 ms gap, 32 bits, 560."""
    b8 = list(b8)
    if fix_sum:
        b8[7] = (b8[7] & 15) | gree_sum(b8) << 4
    blk = [9000, 4500] + pdist_bits(le_bits(b8[:4]) + [0, 1, 0]) + [560, 20000] + pdist_bits(le_bits(b8[4:])) + [560]
    return [0] + skew(blk)

nec = pdist(9000, [0x04, 0xFB, 0x08, 0xF7])
cases = [
    ('NEC 04/08', nec, '04fb08f7', 'NEC'),
    ('NEC + repeat 40 ms', nec + [40000] + skew([9000, 2250, 560]), '04fb08f7', 'NEC'),
    ('Samsung 07/02', pdist(4500, [0x07, 0x07, 0x02, 0xFD]), '070702fd', 'Samsung'),
    ('Sony12 a1 c21', sony(21, 1, 12), '00010015', 'Sony12'),
    ('Sony12 held (3 frames)', sony(21, 1, 12) + [20000] + sony(21, 1, 12)[1:] + [20000] + sony(21, 1, 12)[1:], '00010015', 'Sony12'),
    ('Sony15 a0x97 c0x12', sony(0x12, 0x97, 15), '00970012', 'Sony15'),
    ('Sony20 a0x1a5b c0x3c', sony(0x3c, 0x1a5b, 20), '1a5b003c', 'Sony20'),
    ('RC5 (dropped: not decoded)', rc5(1, 0, 12), None, None),
    ('Gree AC on', gree([0x09, 0x08, 0x60, 0x50, 0, 0xC0, 0, 0]), '09086050', 'Long code - see PC'),
    ('Gree AC off', gree([0x01, 0x08, 0x60, 0x50, 0, 0xC0, 0, 0]), '01086050', 'Long code - see PC'),
    ('Gree AC bad checksum -> NEC', gree([0x09, 0x08, 0x60, 0x50, 0, 0xC0, 0, 0x10], fix_sum=False), '09086050', 'NEC'),
    ('NEC repeat only', [0] + skew([9000, 2250, 560]), None, None),
    ('noise', [0, 300, 7000, 120], None, None),
]
ok = True
up, us = mk(pat), mk(stock)
for name, d, want, label in cases:
    log = run_patched(up, d)
    got = dict(log)
    good = (want is None and not log) or (got.get('show') == want and got.get('label') == label
                                         and [k for k, _ in log] == ['show', 'fill', 'label']
                                         and got.get('fill') == (0, 0x72, 0x80, 0x7F, 0))
    ok &= good
    extra = f"  stock NEC -> {run_stock_nec(us, d[:68])}" if name.startswith('NEC') and want else ''
    print(f"{'PASS' if good else 'FAIL'}  {name:<30} edges={len(d):3d}  {log}{extra}")

# unknown long frame: whole capture dumped in one line, incl. gaps
long_unknown = [0] + skew([3000, 3000] + [500, 1500, 500, 500] * 60 + [500, 30000] + [3000, 3000, 500])
run_patched(up, long_unknown)
txt = ''.join(up.out)
vals = txt.split(':', 1)[1].split()
good = txt.startswith(f'\nIR raw {len(long_unknown) - 1}:') and [int(v) for v in vals] == long_unknown[1:]
ok &= good
print(f"{'PASS' if good else 'FAIL'}  raw dump of {len(long_unknown) - 1} edges incl. 30 ms gap")

# buffer limit: 800 edges -> capture stops at 760, then decodes/dumps normally
run_patched(up, [0] + [600] * 799)
n = int(''.join(up.out).split()[2].rstrip(':'))
good = n == 759
ok &= good
print(f"{'PASS' if good else 'FAIL'}  buffer limit: 800 edges -> dumped {n}")
# first poll after power-on: random RAM, no magic -> silent re-arm, no dump, no display
up.log.clear(); up.out.clear()
up.mem_write(0x20001900, bytes(range(256)) * 6); up.mem_write(0x20001EF8, struct.pack('<HH', 0xBEEF, 0x1234))
up.mem_write(0x20000802, bytes([0x04]))
call(up, POLL)
st = up.mem_read(0x20000802, 1)[0], struct.unpack('<HH', up.mem_read(0x20001EF8, 4))
good = not up.log and not up.out and st[0] & 1 and st[1] == (0, 0x1A5C)
ok &= good
print(f"{'PASS' if good else 'FAIL'}  stale RAM at power-on -> silent re-arm {st}")

# real captures of a Gree-type AC remote recorded with the patched meter, replayed through the ISR
import json, os
real = [json.loads(l) for l in open(os.path.join(os.path.dirname(__file__), 'captures/ac.jsonl'))]
real = [[0] + [int(x) for x in r['fields']['timings_us'].split()] for r in real
        if r['fields'].get('protocol') == 'unknown' and r['fields'].get('edges') == '419']
def ref_decode(t):            # independent python decoder for message 1
    bits = lambda i, n: [1 if t[i + 2 * k + 1] > 1200 else 0 for k in range(n)]
    b1, b2 = bits(3, 35), bits(75, 32)
    by = [sum(b1[8 * j + k] << k for k in range(8)) for j in range(4)] + \
         [sum(b2[8 * j + k] << k for k in range(8)) for j in range(4)]
    return by, gree_sum(by) == by[7] >> 4
nreal = 0
for t in real:
    by, sum_ok = ref_decode(t)
    log = dict(run_patched(up, t))
    line = ''.join(up.out)
    want_line = '\nAC Gree' + ''.join(f' {x:02X}' for x in by) + '\n'
    good = sum_ok and log.get('label') == 'Long code - see PC' and log.get('show') == bytes(by[:4]).hex() and line == want_line
    ok &= good; nreal += 1
    if not good:
        print('FAIL  real AC', bytes(by).hex(), sum_ok, log, repr(line))
print(f"{'PASS' if ok else 'FAIL'}  {nreal} real AC captures from the meter: label, bytes, checksum, UART line")

# long AC transmission: decoded (first block) AND dumped whole
g = gree([0x09, 0x08, 0x60, 0x50, 0, 0xC0, 0, 0x10], fix_sum=False)   # bad checksum
g2 = g + [40000] + g[1:]
log = run_patched(up, g2)
txt = ''.join(up.out)
good = dict(log).get('label') == 'NEC' and txt.startswith(f'\nIR raw {len(g2) - 1}:')
ok &= good
print(f"{'PASS' if good else 'FAIL'}  unknown long frame ({len(g2)} edges): NEC first block + raw dump")
sys.exit(0 if ok else 1)

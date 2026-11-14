#!/usr/bin/env python3
"""PC side of the PIN_DEBUG firmware (firmware/pindump.c): guided session that logs which GPIO pins
change while you press keys and switch modes, to finish mapping the LCR-P1 board.

    PYTHONPATH=app python3 firmware/pinlog.py [--log pins.txt] [--replay pins.txt]

It walks through a few steps (press Enter after each); every pin change is printed with the step
it happened in, and the raw serial stream goes to the log file. Screen text mirrored by the
firmware is shown dimmed for context.
"""
import argparse
import os
import select
import sys
import time

REGS = ['in A', 'in B', 'in C', 'in F', 'out A', 'out B', 'out C', 'out F']
PORTS = 'ABCF'
KNOWN = {  # from hwmap (stock firmware analysis)
    'PA1': 'probe 1 direct', 'PA3': 'probe 2 direct', 'PA6': 'probe 3 direct',
    'PA4': 'probe 1 680R', 'PA7': 'probe 2 680R', 'PB2': 'probe 3 680R',
    'PA5': 'probe 1 470k', 'PB1': 'probe 2 470k', 'PB10': 'probe 3 470k',
    'PA0': 'ADC ref/Vcc?', 'PA2': 'ADC Zener HV', 'PB0': 'ADC battery',
    'PA8': 'power latch?', 'PA9': 'UART TX', 'PA10': 'UART RX', 'PA11': 'IR receiver',
    'PB13': 'LCD SCK', 'PB15': 'LCD MOSI',
}
STEPS = [
    'Meter ON, idle screen, nothing in the socket. Wait ~3 s.',
    'Press TEST once (short), nothing in the socket. Wait for the result screen.',
    'Put a resistor (any, e.g. 1k) in 1-2 and press TEST. Wait for the result. Remove it.',
    'Press the ZENER (high-voltage) button to enter Zener mode. Wait ~3 s.',
    'In Zener mode press TEST (socket empty is fine). Wait for the result.',
    'Leave Zener mode (press ZENER again, or TEST - whatever the meter needs). Wait ~3 s.',
    'Slide the mode switch to IR. Wait ~3 s.',
    'Slide the mode switch back. Wait ~3 s.',
    'Press TEST briefly once more (idle check).',
    'Hold TEST to power off (the last lines may be cut off - that is expected).',
]
DIM, BOLD, OFF = '\033[2m', '\033[1m', '\033[0m'


def pin(reg, bit):
    name = f'P{PORTS[reg & 3]}{bit}'
    return name, KNOWN.get(name, '')


class Decoder:
    def __init__(self, out):
        self.out, self.snap, self.busy, self.step = out, None, None, 0

    def line(self, s):
        f = s.split()
        if not f:
            return
        try:
            if f[0] == '#E' and len(f) == 5:
                t, reg, v, x = int(f[1]), int(f[2]), int(f[3], 16), int(f[4], 16)
                for b in range(16):
                    if x >> b & 1:
                        name, role = pin(reg, b)
                        kind = 'in ' if reg < 4 else 'out'
                        self.out(f'  [step {self.step}] t={t / 200:7.2f}s  {BOLD}{name:5}{OFF} {kind} '
                                 f'-> {v >> b & 1}   {role}')
                return
            if f[0] == '#S' and len(f) == 14:
                vals = [int(a, 16) for a in f[1:13]]
                if self.snap and vals[8:12] != self.snap[8:12]:
                    for p in range(4):
                        for b in range(16):
                            o, n = self.snap[8 + p] >> 2 * b & 3, vals[8 + p] >> 2 * b & 3
                            if o != n:
                                name = f'P{PORTS[p]}{b}'
                                m = ['input', 'output', 'alt-func', 'analog']
                                self.out(f'  [step {self.step}] {name:5} mode {m[o]} -> {m[n]}   {KNOWN.get(name, "")}')
                if int(f[13]):
                    self.out(f'  ({int(f[13])} events dropped so far)')
                self.snap = vals
                return
            if f[0] == '#B' and len(f) == 9:
                masks = [int(a, 16) for a in f[1:]]
                names = [pin(r, b)[0] + ('(in)' if r < 4 else '(out)')
                         for r in range(8) for b in range(16) if masks[r] >> b & 1]
                key = ' '.join(names)
                if key != self.busy:
                    self.out(f'  [step {self.step}] busy (toggling fast): {key}')
                    self.busy = key
                return
        except ValueError:
            pass
        if s.startswith('#'):
            return
        if s.strip():
            self.out(f'{DIM}  screen: {s.strip()}{OFF}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--log', default=time.strftime('pins-%Y%m%d-%H%M%S.txt'))
    ap.add_argument('--replay', help='decode a saved raw log instead of reading the meter')
    a = ap.parse_args()
    dec = Decoder(print)
    if a.replay:
        for l in open(a.replay, errors='replace'):
            if l.startswith('== step '):
                dec.step = int(l.split()[2])
                print(f'== step {dec.step}: {STEPS[dec.step - 1] if dec.step <= len(STEPS) else ""}')
            else:
                dec.line(l.rstrip('\n'))
        return
    from lcrp1.port import find_port, open_port
    print('Waiting for the meter (switch it on, USB connected)...')
    while not (dev := find_port()):
        time.sleep(0.5)
    fd = open_port(dev)
    log = open(a.log, 'w')
    print(f'{dev} open, raw log -> {a.log}\n')
    buf = b''
    step = 0

    def next_step():
        nonlocal step
        step += 1
        dec.step = step
        log.write(f'== step {step}\n'); log.flush()
        if step > len(STEPS):
            return False
        print(f'\n{BOLD}== step {step}/{len(STEPS)}: {STEPS[step - 1]}{OFF}\n   (Enter when done)')
        return True

    next_step()
    try:
        while True:
            r, _, _ = select.select([fd, sys.stdin], [], [], 1.0)
            if sys.stdin in r:
                sys.stdin.readline()
                if not next_step():
                    break
            if fd in r:
                try:
                    data = os.read(fd, 4096)
                except OSError:
                    data = b''
                if not data:
                    print('  (port closed - meter off?)')
                    if step >= len(STEPS):
                        break
                    os.close(fd)
                    while not (dev := find_port()):
                        time.sleep(0.5)
                    fd = open_port(dev)
                    continue
                buf += data
                *lines, buf = buf.split(b'\n')
                for l in lines:
                    s = l.decode('latin-1').rstrip('\r')
                    log.write(s + '\n')
                    dec.line(s)
                log.flush()
    except KeyboardInterrupt:
        pass
    print(f'\nDone. Raw log: {os.path.abspath(a.log)}')


if __name__ == '__main__':
    main()

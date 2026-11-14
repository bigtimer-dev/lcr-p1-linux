"""lcrp1-log: print / save LCR-P1 measurements from the patched firmware's serial output.

Usage:
  lcrp1-log [PORT] [--csv out.csv] [--jsonl out.jsonl] [--raw raw.txt]
  lcrp1-log --replay raw.txt        # parse a saved raw capture
  lcrp1-log --send R3 --csv parts.csv   # test every 3 s after the previous test (hands-free sorting)
Commands for --send (patched firmware with serial commands): T test, R<s> repeat every s seconds
(R0 off), A<s> auto power-off after s seconds (A0 off), O power off, ? status.
PORT defaults to the meter's CH340, found automatically (and re-found after power cycles).
"""
import argparse
import csv
import json
import os
import sys
import threading

from .parse import Parser, fmt, si
from .port import Reader


def main(argv=None):
    ap = argparse.ArgumentParser(prog='lcrp1-log', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('port', nargs='?')
    ap.add_argument('--csv')
    ap.add_argument('--jsonl')
    ap.add_argument('--raw')
    ap.add_argument('--replay', help='parse a saved raw capture instead of the port')
    ap.add_argument('--send', action='append', default=[], metavar='CMD',
                    help='send a command once connected (repeatable)')
    a = ap.parse_args(argv)

    csvw = cf = None
    if a.csv:
        new = not os.path.exists(a.csv)
        cf = open(a.csv, 'a', newline='')
        csvw = csv.writer(cf)
        if new:
            csvw.writerow(['time', 'component', 'pins', 'field', 'text', 'value', 'unit'])
    jf = open(a.jsonl, 'a') if a.jsonl else None
    rawf = open(a.raw, 'ab') if a.raw else None
    lock = threading.Lock()

    def emit(r):
        with lock:
            print(fmt(r), flush=True)
            if 'event' in r:
                return
            r['values'] = {k: {'value': p[0], 'unit': p[1]} for k, v in r['fields'].items()
                           if isinstance(v, str) and (p := si(v))}
            if jf:
                jf.write(json.dumps(r) + '\n')
                jf.flush()
            if csvw:
                for k, v in r['fields'].items():
                    if k in ('extra', '_hex'):
                        continue
                    p = si(v) or ('', '')
                    csvw.writerow([r['time'], r['component'], r['pins'], k, v, p[0], p[1]])
                cf.flush()

    if a.replay:
        p = Parser(emit)
        for ln in open(a.replay, encoding='latin-1').read().split('\n'):
            p.line(ln)
        p.flush()
        return 0

    def raw(data):
        if rawf:
            rawf.write(data)
            rawf.flush()

    def status(text, ok):
        print('#', text, file=sys.stderr, flush=True)
        if ok:
            for c in a.send:        # also re-sent after the meter was switched off and on
                rd.send(c)

    rd = Reader(emit, status, raw, a.port)
    rd.start()
    try:
        while rd.is_alive():
            rd.join(0.5)
    except KeyboardInterrupt:
        rd.stop()
        rd.join(2)
    return 0


if __name__ == '__main__':
    sys.exit(main())


def cmd_main(argv=None):
    """lcrp1 cmd CMD...: send commands to the meter and print its replies (T, R<s>, A<s>, O, ?)."""
    import time
    from .port import find_port, open_port
    import select as _select
    argv = list(sys.argv[2:] if argv is None else argv)
    if not argv:
        print(cmd_main.__doc__)
        return 2
    dev = find_port()
    if not dev:
        print('Meter not found (switch it on, USB connected).', file=sys.stderr)
        return 1
    from .port import Pacer
    fd = open_port(dev)
    got, buf = [], b''
    p = Parser(lambda r: got.append(r) if r.get('event') == 'control' else None)
    try:
        for c in argv:
            n = len(got)
            pacer = Pacer()
            pacer.add(c)
            end = time.time() + 15          # a running test delays the answer
            while len(got) == n and time.time() < end:
                pacer.pump(fd)
                if _select.select([fd], [], [], 0.02)[0]:
                    buf += pacer.feed(os.read(fd, 512))
                    *lines, rest = buf.split(b'\n')
                    buf = rest
                    for ln in lines:
                        p.line(ln.decode('latin-1'))
            if len(got) == n:
                print(f'#{c}: no answer (firmware without serial commands?)', file=sys.stderr)
                return 1
            print(fmt(got[-1]))
    finally:
        os.close(fd)
    return 0

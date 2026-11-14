"""lcrp1-log: print / save LCR-P1 measurements from the patched firmware's serial output.

Usage:
  lcrp1-log [PORT] [--csv out.csv] [--jsonl out.jsonl] [--raw raw.txt]
  lcrp1-log --replay raw.txt        # parse a saved raw capture
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

    rd = Reader(emit, lambda text, ok: print('#', text, file=sys.stderr, flush=True), raw, a.port)
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

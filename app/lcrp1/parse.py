"""Turn the LCR-P1 patched firmware's serial text (everything drawn on the LCD, plus
"IR raw" / "AC Gree" lines) into one record per measurement."""
import re
import time

TITLES = ('Resistance', 'Two Resistance', 'Capacitance', 'Inductor', 'Diode', 'Two diode',
          'Voltage Regulator', 'Voltage Regulation', 'Transistor', 'Field-Effect', 'Thyristor',
          'Battery', 'Infrared Decoding', 'Unknown/No Component')
NOISE = ('Test-In-Process', 'Calibration page', 'Waiting')
PREFIX = {'p': 1e-12, 'n': 1e-9, 'u': 1e-6, 'm': 1e-3, '': 1.0, 'k': 1e3, 'K': 1e3, 'M': 1e6, 'G': 1e9}
NUM = re.compile(r'^\s*([-+]?\d+(?:\.\d+)?)\s*([pnumkKMG]?)\s*([A-Za-z%]*)\s*$')


def si(text):
    """'988.0Kohm' -> (988000.0, 'ohm'); '4712' -> (4712.0, ''); else None."""
    m = NUM.match(text)
    if not m:
        return None
    val, pre, unit = m.groups()
    if pre and not unit and pre in 'mM':     # bare 'm'/'M' is ambiguous; keep as unit-less prefix
        pass
    return float(val) * PREFIX[pre], unit


class Parser:
    def __init__(self, emit):
        self.emit, self.rec, self.skip_label = emit, None, False

    def flush(self):
        r, self.rec = self.rec, None
        if r and r['fields'] and set(r['fields']) != {'_hex'} or r and r['component'].startswith('Unknown'):
            self.emit(r)

    def line(self, ln):
        # the idle screen's pin diagram ("1 2 3 1 2 3 2") is drawn on the same row as the
        # last result line, so it can arrive glued to it
        ln = re.sub(r'(?:^|\s+)[123](?: [123]){6}\s*$', '', ln.strip())
        # firmware's own debug prints during pin probing ("===Check Pin===" / "3, 1, 2")
        ln = re.sub(r'=+Check Pin=+', '', ln).strip()
        if not ln or re.fullmatch(r'K A A(?: [123]){4}', ln) or re.fullmatch(r'\d, \d, \d', ln):
            return
        m = re.fullmatch(r'#([A-Z?!]) R=(\d+) A=(\d+) P=(\d)', ln)
        if m:                                       # reply to a serial command (patch v1.1.0u)
            self.emit({'event': 'control', 'cmd': m.group(1), 'repeat_s': int(m.group(2)),
                       'autooff_s': int(m.group(3)), 'ir_page': m.group(4) == '1'})
            return
        m = re.fullmatch(r'AC Gree((?: [0-9A-F]{2}){8})(?: fan \d)?', ln)
        if m:   # Gree/Kelvinator AC message 1 (patch v1.1.0u); field meanings are a best guess (Gree layout)
            by = [int(x, 16) for x in m.group(1).split()]
            mode = by[0] & 7
            self.emit({'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'component': 'IR', 'pins': '',
                       'fields': {'protocol': 'Gree AC', 'power': 'on' if by[0] & 8 else 'off',
                                  'mode': ['auto', 'cool', 'dry', 'fan', 'heat'][mode] if mode < 5 else str(mode),
                                  'temp_C': str((by[1] & 15) + 16), 'fan': str((by[0] >> 4) & 3),
                                  'light': 'on' if by[2] & 0x20 else 'off',     # byte2 bit5 (seen toggling)
                                  'bytes': m.group(1).strip()}})
            if self.rec and self.rec['component'] == 'Infrared Decoding':
                self.rec['fields'].pop('_hex', None)
            self.skip_label = True
            return
        m = re.fullmatch(r'IR raw (\d+):((?: \d+)+)', ln)
        if m:                                       # undecoded IR frame, us: mark space mark ...
            self.emit({'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'component': 'IR', 'pins': '',
                       'fields': {'protocol': 'unknown', 'edges': m.group(1), 'timings_us': m.group(2).strip()}})
            return
        if ln.startswith('--- V:'):
            self.flush()
            self.emit({'event': 'boot', 'version': ln.strip('- ').split(':', 1)[1].strip()})
            return
        if any(ln.startswith(n) for n in NOISE):
            self.flush()
            return
        if any(ln.startswith(t) for t in TITLES):
            if self.rec and self.rec['component'] == ln == 'Infrared Decoding':
                return                              # IR title is drawn twice
            self.flush()
            self.rec = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'component': ln, 'pins': '', 'fields': {}}
            return
        if self.rec is not None and (ln.startswith('Tube') or ln.startswith('/')):
            self.rec['component'] += ' ' + ln       # 2nd title line: Tube(N-E-MOS), /Or damaged
            return
        if self.rec is None and re.fullmatch(r'0x[0-9A-Fa-f]+', ln):
            # logger started while the meter was already on the IR page (title not seen)
            self.rec = {'time': '', 'component': 'Infrared Decoding', 'pins': '', 'fields': {}}
        if self.rec is None:
            return                                  # idle-screen pin diagram etc.
        if self.rec['component'] == 'Infrared Decoding':
            # IR page: title once, then per frame two hex values + protocol label (patch v1.1.0u)
            if ln in ('Address Code', 'Command Code'):
                return
            if re.fullmatch(r'0x[0-9A-Fa-f]+', ln):
                self.rec['fields'].setdefault('_hex', []).append(ln)
                return
            if re.fullmatch(r'Long code - see PC|\d\dC (Auto|Cool|Dry|Fan|Heat|M\d)|AC off', ln):
                self.rec['fields'].pop('_hex', None)  # screen label of an AC frame, already logged
                return
            if re.fullmatch(r'(NEC|Samsung|Sony\d+|RC5)', ln):
                hx = self.rec['fields'].pop('_hex', [])
                if len(hx) >= 2:
                    self.emit({'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'component': 'IR', 'pins': '',
                               'fields': {'protocol': ln, 'address': hx[-2], 'command': hx[-1]}})
                return
        if re.fullmatch(r'[123](?: +[123]){1,2}', ln):
            self.rec['pins'] = '-'.join(ln.split())
        elif ':' in ln:
            # a row can hold several "label: value" pairs
            for k, v in re.findall(r'([A-Za-z_][\w ]*?):\s*([^:]*?)(?=\s+[A-Za-z_][\w ]*:|$)', ln):
                self.rec['fields'][k.strip()] = v.strip()
        else:
            self.rec['fields'].setdefault('extra', []).append(ln)


def fmt(r):
    if r.get('event') == 'control':
        a = f"{r['autooff_s']} s" if r['autooff_s'] else 'off'
        rep = f"every {r['repeat_s']} s" if r['repeat_s'] else 'off'
        return f"# meter: repeat {rep}, auto power-off {a}" + ('  (IR page)' if r['ir_page'] else '')
    if 'event' in r:
        return f"# meter booted, firmware {r.get('version')}"
    f = '  '.join(f'{k}={v}' for k, v in r['fields'].items() if k != 'extra')
    return f"{r['time']}  {r['component']:<24} {('pins ' + r['pins']) if r['pins'] else '':<10} {f}"


def summary(r):
    """One-line value summary of a record, for lists."""
    f = r['fields']
    if r['component'] == 'IR':
        p = f.get('protocol', '')
        if p == 'Gree AC':
            return f"Gree AC  {f.get('bytes', '')}"
        if p == 'unknown':
            return f"unknown, {f.get('edges')} edges (raw timings)"
        return f"{p}  {f.get('address', '')} / {f.get('command', '')}"
    return '  '.join(f'{k} {v}' for k, v in f.items() if k not in ('extra', '_hex'))

"""GTK4 / libadwaita window: live result, history, CSV export, firmware update."""
import csv
import os
import sys
import threading
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from .flash import check, flash  # noqa: E402
from .parse import Parser, si, summary  # noqa: E402
from .port import Reader, find_port  # noqa: E402

APP_ID = 'io.github.bigtimer_dev.LcrP1'

CSS = b"""
.value-big { font-size: 34px; font-weight: 700; font-feature-settings: "tnum"; }
.value-label { font-size: 13px; }
.component { font-size: 22px; font-weight: 700; }
.mono { font-family: monospace; }
"""


def pretty_summary(r):
    if r['component'] == 'IR':
        return summary(r).split('  ', 1)[-1] if r['fields'].get('protocol') != 'unknown' else summary(r)
    return '   '.join(f'{k} {pretty(v)}' for k, v in r['fields'].items() if isinstance(v, str) and k != '_hex')


def pretty(v):
    """Display units the way they are written ('988.0Kohm' -> '988.0 kΩ')."""
    import re
    m = re.fullmatch(r'([-+]?\d+(?:\.\d+)?)\s*([pnumkKMG]?)(ohm|F|H|V|A|mA|mV)?', v.strip())
    if not m:
        return v
    num, pre, unit = m.groups()
    pre = {'K': 'k', 'u': 'µ'}.get(pre, pre)
    unit = 'Ω' if unit == 'ohm' else (unit or '')
    return f'{num} {pre}{unit}'.rstrip()


class Window(Adw.ApplicationWindow):
    def __init__(self, app, replay=None):
        super().__init__(application=app, title='LCR-P1')
        self.set_default_size(560, 720)
        self.records = []
        self.reader = None

        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        self.title = Adw.WindowTitle(title='LCR-P1', subtitle='Starting…')
        hb.set_title_widget(self.title)

        export = Gtk.Button(icon_name='document-save-symbolic', tooltip_text='Export history as CSV')
        export.connect('clicked', self.on_export)
        hb.pack_start(export)
        clear = Gtk.Button(icon_name='edit-clear-all-symbolic', tooltip_text='Clear history')
        clear.connect('clicked', self.on_clear)
        hb.pack_start(clear)

        menu = Gio.Menu()
        menu.append('Update Firmware…', 'win.firmware')
        menu.append('About LCR-P1', 'app.about')
        hb.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu))
        act = Gio.SimpleAction.new('firmware', None)
        act.connect('activate', lambda *a: self.on_firmware())
        self.add_action(act)
        tv.add_top_bar(hb)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_top=12, margin_bottom=12, margin_start=16, margin_end=16)

        # latest result
        self.comp = Gtk.Label(label='No reading yet', xalign=0, wrap=True, css_classes=['component'])
        self.pins = Gtk.Label(label='Insert a part and press TEST', xalign=0, css_classes=['dim-label'])
        self.values = Gtk.Grid(column_spacing=16, row_spacing=4)
        self.extra = Gtk.Label(xalign=0, wrap=True, selectable=True, css_classes=['dim-label', 'mono'],
                               visible=False)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for w in (self.comp, self.pins, self.values, self.extra):
            head.append(w)
        box.append(head)
        box.append(Gtk.Separator())

        # history
        hl = Gtk.Label(label='History', xalign=0, css_classes=['heading'])
        box.append(hl)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=['boxed-list'])
        self.list.connect('row-selected', self.on_row)
        self.list.set_placeholder(Gtk.Label(label='Results appear here', css_classes=['dim-label'],
                                            margin_top=24, margin_bottom=24))
        sc = Gtk.ScrolledWindow(vexpand=True, child=self.list)
        box.append(sc)

        self.toast = Adw.ToastOverlay(child=box)
        tv.set_content(self.toast)
        tv.add_bottom_bar(self.build_controls())
        self.set_content(tv)
        self.connect('close-request', self.on_close)

        if replay:
            self.set_status(f'Replay of {os.path.basename(replay)}', True)
            p = Parser(lambda r: self.add_record(r))
            for ln in open(replay, encoding='latin-1').read().split('\n'):
                p.line(ln)
            p.flush()
        else:
            self.start_reader()

    # ------------------------------------------------------------------ meter control
    def build_controls(self):
        """Bottom bar: test / repeat / auto power-off / power off (firmware with serial commands)."""
        self._updating = False
        self._pending = {}
        bar = Gtk.Box(spacing=8, margin_top=8, margin_bottom=8, margin_start=12, margin_end=12)
        self.test_btn = Gtk.Button(label='Test', css_classes=['suggested-action'],
                                   tooltip_text='Run a test, as pressing TEST on the meter')
        self.test_btn.connect('clicked', lambda *_: self.send('T'))
        bar.append(self.test_btn)
        bar.append(Gtk.Label(label='Repeat', margin_start=8))
        self.rep = Gtk.SpinButton.new_with_range(0, 600, 1)
        self.rep.set_tooltip_text('Test again this many seconds after the previous test finished (0 = off). '
                                  'Insert parts one after another for hands-free logging.')
        self.rep.connect('value-changed', lambda w: self.queue_send('R', int(w.get_value())))
        bar.append(self.rep)
        bar.append(Gtk.Label(label='s', css_classes=['dim-label']))
        bar.append(Gtk.Box(hexpand=True))
        bar.append(Gtk.Label(label='Auto-off'))
        self.aoff = Gtk.SpinButton.new_with_range(0, 60, 1)
        self.aoff.set_tooltip_text('Switch the meter off after this many idle minutes (0 = off; '
                                   'the meter still switches off after 60 min). Resets to the '
                                   'firmware default when the meter is switched on.')
        self.aoff.connect('value-changed', lambda w: self.queue_send('A', int(w.get_value()) * 60))
        bar.append(self.aoff)
        bar.append(Gtk.Label(label='min', css_classes=['dim-label']))
        self.off_btn = Gtk.Button(icon_name='system-shutdown-symbolic', tooltip_text='Switch the meter off',
                                  css_classes=['destructive-action'], margin_start=8)
        self.off_btn.connect('clicked', self.on_power_off)
        bar.append(self.off_btn)
        self.controls = (self.test_btn, self.rep, self.aoff, self.off_btn)
        self.set_controls(False)
        return bar

    def set_controls(self, on):
        for w in self.controls:
            w.set_sensitive(on)

    def send(self, cmd):
        if not (self.reader and self.reader.send(cmd)):
            self.toast.add_toast(Adw.Toast(title='Meter not connected'))

    def queue_send(self, letter, value):
        """Spin buttons: send once the value has settled for 0.4 s."""
        if self._updating:
            return
        old = self._pending.pop(letter, None)
        if old:
            GLib.source_remove(old)

        def fire():
            self._pending.pop(letter, None)
            self.send(f'{letter}{value}')
            return False
        self._pending[letter] = GLib.timeout_add(400, fire)

    def on_control(self, r):
        """Reply from the meter: '#<cmd> R=.. A=.. P=..'."""
        self.set_controls(True)
        self._updating = True
        self.rep.set_value(r['repeat_s'])
        self.aoff.set_value(round(r['autooff_s'] / 60))
        self._updating = False
        if r['cmd'] == 'T' and r['ir_page']:
            self.toast.add_toast(Adw.Toast(title='The meter is on the IR page: switch to component test'))
        elif r['cmd'] == 'O':
            self.toast.add_toast(Adw.Toast(title='Meter switched off'))

    def on_power_off(self, *_):
        a = Adw.AlertDialog(heading='Switch the meter off?',
                            body='Press TEST on the meter to switch it on again.')
        a.add_response('cancel', 'Cancel')
        a.add_response('off', 'Switch Off')
        a.set_response_appearance('off', Adw.ResponseAppearance.DESTRUCTIVE)
        a.connect('response', lambda _a, resp: resp == 'off' and self.send('O'))
        a.present(self)

    # ------------------------------------------------------------------ link
    def start_reader(self):
        self.reader = Reader(lambda r: GLib.idle_add(self.add_record, r),
                             lambda t, ok: GLib.idle_add(self.set_status, t, ok))
        self.reader.start()

    def set_status(self, text, ok):
        self.title.set_subtitle(text)
        if ok:     # ask the firmware for its settings; the reply enables the controls
            GLib.timeout_add(300, lambda: (self.reader and self.reader.send('?')) and False)
        else:
            self.set_controls(False)
        return False

    def on_close(self, *_):
        if self.reader:
            self.reader.stop()
        return False

    # ------------------------------------------------------------------ records
    def add_record(self, r):
        if r.get('event') == 'control':
            self.on_control(r)
            return False
        if 'event' in r:
            self.toast.add_toast(Adw.Toast(title=f"Meter started, firmware {r.get('version')}"))
            if self.reader:
                GLib.timeout_add(1500, lambda: self.reader.send('?') and False)
            return False
        if not r.get('time'):
            r['time'] = time.strftime('%Y-%m-%d %H:%M:%S')
        self.records.append(r)
        row = Adw.ActionRow(title=GLib.markup_escape_text(self.title_of(r)),
                            subtitle=GLib.markup_escape_text(f"{r['time'][11:]}   {pretty_summary(r)}"))
        row.record = r
        self.list.prepend(row)
        self.show_record(r)
        return False

    @staticmethod
    def title_of(r):
        t = r['component']
        if t == 'IR':
            t = f"IR · {r['fields'].get('protocol', '')}"
        if r.get('pins'):
            t += f"   pins {r['pins']}"
        return t

    def on_row(self, _lb, row):  # noqa: D401
        if row is not None:
            self.show_record(row.record)

    def show_record(self, r):
        f = r['fields']
        comp = r['component']
        if comp == 'IR':
            comp = f"IR: {f.get('protocol', '')}"
        self.comp.set_label(comp)
        self.pins.set_label(f"pins {r['pins']}   ·   {r['time']}" if r.get('pins') else r['time'])
        while (c := self.values.get_first_child()) is not None:
            self.values.remove(c)
        rows = [(k, v) for k, v in f.items() if k not in ('extra', '_hex', 'timings_us', 'protocol')]
        if comp.startswith('IR: Gree'):
            rows = [('code', f.get('bytes', ''))] + [(k, f[k]) for k in ('power', 'mode', 'temp_C', 'fan', 'light') if k in f]
        for i, (k, v) in enumerate(rows):
            self.values.attach(Gtk.Label(label=k, xalign=0, css_classes=['value-label', 'dim-label']), 0, i, 1, 1)
            big = k in ('value', 'Cap', 'L_value', 'R_value', 'hFE', 'address', 'command', 'code') or len(rows) <= 3
            self.values.attach(Gtk.Label(label=pretty(v), xalign=0, selectable=True,
                                         css_classes=['value-big' if big else 'title-4']), 1, i, 1, 1)
        extra = []
        if comp.startswith('IR: Gree'):
            extra.append('Field meanings are a best guess (Gree layout); the code bytes are exact.')
        if 'timings_us' in f:
            extra.append('Raw timings (µs, mark space …):\n' + f['timings_us'])
        if f.get('extra'):
            extra.append(' / '.join(f['extra']))
        self.extra.set_label('\n'.join(extra))
        self.extra.set_visible(bool(extra))

    def on_clear(self, *_):
        self.records.clear()
        self.list.remove_all()
        self.comp.set_label('No reading yet')
        self.pins.set_label('Insert a part and press TEST')
        while (c := self.values.get_first_child()) is not None:
            self.values.remove(c)
        self.extra.set_visible(False)

    # ------------------------------------------------------------------ export
    def on_export(self, *_):
        if not self.records:
            self.toast.add_toast(Adw.Toast(title='Nothing to export yet'))
            return
        d = Gtk.FileDialog(title='Export CSV', initial_name=time.strftime('lcr-p1_%Y%m%d_%H%M%S.csv'))
        d.save(self, None, self._export_done)

    def _export_done(self, d, res):
        try:
            gf = d.save_finish(res)
        except GLib.Error:
            return
        path = gf.get_path()
        with open(path, 'w', newline='') as fh:
            w = csv.writer(fh)
            w.writerow(['time', 'component', 'pins', 'field', 'text', 'value', 'unit'])
            for r in self.records:
                for k, v in r['fields'].items():
                    if k in ('extra', '_hex') or not isinstance(v, str):
                        continue
                    p = si(v) or ('', '')
                    w.writerow([r['time'], r['component'], r['pins'], k, v, p[0], p[1]])
        self.toast.add_toast(Adw.Toast(title=f'Saved {os.path.basename(path)}'))

    # ------------------------------------------------------------------ firmware
    def on_firmware(self):
        d = Gtk.FileDialog(title='Choose LCR-P1 firmware (.bin)')
        flt = Gtk.FileFilter(name='Firmware (*.bin, *.BIN)')
        flt.add_pattern('*.bin')
        flt.add_pattern('*.BIN')
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(flt)
        d.set_filters(store)
        d.open(self, None, self._fw_chosen)

    def _fw_chosen(self, d, res):
        try:
            path = d.open_finish(res).get_path()
        except GLib.Error:
            return
        img = open(path, 'rb').read()
        errs = check(img)
        if errs:
            a = Adw.AlertDialog(heading='Not a valid LCR-P1 image', body='\n'.join(errs))
            a.add_response('ok', 'OK')
            a.present(self)
            return
        a = Adw.AlertDialog(
            heading='Update firmware?',
            body=f'{os.path.basename(path)} ({len(img)} bytes)\n\n'
                 'Put the meter in update mode first: hold ZENER, then press TEST.\n'
                 'The calibration is kept. If anything goes wrong, enter update mode again and '
                 'flash the stock file.')
        a.add_response('cancel', 'Cancel')
        a.add_response('flash', 'Update')
        a.set_response_appearance('flash', Adw.ResponseAppearance.SUGGESTED)
        a.connect('response', lambda _a, resp: resp == 'flash' and self._fw_start(img))
        a.present(self)

    def _fw_start(self, img):
        if self.reader and not self.reader.pause():
            self.toast.add_toast(Adw.Toast(title='Could not release the serial port'))
            self.reader.resume()
            return
        dev = find_port()
        if not dev:
            self.toast.add_toast(Adw.Toast(title='Meter not found: is it on and in update mode?'))
            if self.reader:
                self.reader.resume()
            return
        dlg = Adw.Dialog(title='Updating firmware', can_close=False, content_width=360)
        bar = Gtk.ProgressBar(show_text=True)
        lbl = Gtk.Label(label=f'Sending to {dev}…', wrap=True)
        bx = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=24, margin_bottom=24,
                     margin_start=24, margin_end=24)
        bx.append(lbl)
        bx.append(bar)
        dlg.set_child(bx)
        dlg.present(self)

        def prog(i, n):
            GLib.idle_add(bar.set_fraction, i / n)

        def work():
            try:
                ok, msg = flash(img, dev, prog)
            except OSError as e:
                ok, msg = False, f'Serial error: {e.strerror or e}'
            GLib.idle_add(done, ok, msg)

        def done(ok, msg):
            dlg.set_can_close(True)
            dlg.force_close()
            a = Adw.AlertDialog(heading='Update finished' if ok else 'Update failed', body=msg)
            a.add_response('ok', 'OK')
            a.present(self)
            if self.reader:
                self.reader.resume()
            return False

        threading.Thread(target=work, daemon=True).start()


class App(Adw.Application):
    def __init__(self, replay=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.replay = replay
        act = Gio.SimpleAction.new('about', None)
        act.connect('activate', self.on_about)
        self.add_action(act)

    def do_startup(self):
        Adw.Application.do_startup(self)
        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        from gi.repository import Gdk
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        Window(self, self.replay).present()

    def on_about(self, *_):
        a = Adw.AboutDialog(application_name='LCR-P1', version=__version__, developer_name='bigtimer-dev',
                            application_icon=APP_ID,
                            comments='Live readings, history and firmware update for the FNIRSI LCR-P1 '
                                     'running the serial-output patch (v1.1.0u).')
        a.present(self.get_active_window())


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    replay = None
    if '--replay' in argv:
        i = argv.index('--replay')
        replay = argv[i + 1]
        del argv[i:i + 2]
    if '--version' in argv:
        print(__version__)
        return 0
    return App(replay).run(argv[:1])

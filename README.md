# FNIRSI LCR-P1 on Linux: firmware patch + companion app

Tools for the **FNIRSI LCR-P1** component tester:

- **A firmware patch** for the stock v1.1.0 firmware that sends every reading over the meter's USB-C
  serial port, decodes many more infrared remotes, and lets the meter stay on longer.
- **A Linux updater** for FNIRSI's bootloader (replaces the Windows-only `BootTool.exe`).
- **`lcrp1`**, a GTK4 / libadwaita app with live readings, history, CSV export and firmware update, plus
  **`lcrp1-log`** for the terminal.

Not affiliated with FNIRSI. FNIRSI's firmware is not included: you download it from them and the build
patches that exact file.

## What the patched firmware adds

| | Stock v1.1.0 | Patched (v1.1.0u) |
|---|---|---|
| USB serial output | none | every line drawn on the screen (115200 8N1) |
| IR decoding | NEC only (32 bits, 68 edges max) | NEC, Samsung32, Sony SIRC 12/15/20, Philips RC5/RC5X, Gree/Kelvinator AC |
| AC remotes | first 4 bytes, as if NEC | first 4 bytes on screen + `Long code - see PC`; all 8 bytes over USB |
| Unknown remotes | nothing | raw mark/space timings over USB (`IR raw <n>: …`), up to 760 edges |
| Auto power-off | 60 s | 5 min (`AUTOOFF_S`, 30 s – 60 min) |
| Low-battery shutdown | ~3.5 V | 3.4 V (`LOWBAT_MV`, 3.3 – 3.6 V) |

Measurement code is untouched. The image is the same size as stock and the calibration is kept.

Tested on a real LCR-P1: resistors and capacitors read the same as with stock firmware; NEC (LG codes
from a Google TV remote), Samsung TV and Gree-type AC remotes decode on the meter. Sony and RC5 are
tested in the emulator only so far.

## Build the firmware

Needs `arm-none-eabi-gcc` / binutils and Python 3.

1. Download **LCR-P1 Firmware v1.1.0** from FNIRSI and put `APP_LCR-P1-EN_V1.1.0_260324.BIN` in `fw/`.
2. Build:
   ```sh
   python3 firmware/make_patch.py            # -> build/APP_LCR-P1-EN_V1.1.0u.BIN
   AUTOOFF_S=600 LOWBAT_MV=3500 python3 firmware/make_patch.py   # other settings
   NO_IR=1 python3 firmware/make_patch.py    # serial output + settings, stock IR
   ```
   The build checks the stock file's SHA-256 and every patched location before changing it.
3. Optional, run the emulator tests (they drive the patched code with real IR recordings):
   ```sh
   python3 -m venv .venv && .venv/bin/pip install unicorn
   ./firmware/run_tests.sh
   ```

## Flash it

On the meter: **hold ZENER, then press TEST** to enter update mode. Then either use the app
(menu → *Update Firmware…*) or:

```sh
PYTHONPATH=app python3 -m lcrp1 flash build/APP_LCR-P1-EN_V1.1.0u.BIN     # or: lcrp1 flash … once installed
```

`Update successful (reply ac 55 03 04)` means done; the meter restarts. To go back, flash FNIRSI's stock
file the same way. The bootloader is not touched by updates, so update mode always works.

Your user needs access to the serial port (`uucp` group on Arch, `dialout` on Debian/Ubuntu).

## The app

```sh
./app/install.sh              # ~/.local/bin/lcrp1, lcrp1-log, launcher entry "LCR-P1 Component Tester"
./app/install.sh --uninstall
```

- Finds the meter's CH340 by itself and reconnects when the meter is switched off and on.
- Latest result in large type (`360.6 Ω`, `988.0 kΩ`, IR codes, full AC code), history, CSV export.
- *Update Firmware…* checks the file, then flashes with a progress bar.
- `lcrp1 --replay capture.txt` shows a saved raw capture without a meter.

`lcrp1-log [--csv out.csv] [--jsonl out.jsonl] [--raw raw.txt]` prints one line per measurement:

```
2026-11-13 20:29:44  Resistance   pins 1-2   value=360.5ohm
2026-11-13 20:20:51  IR           protocol=NEC  address=0x04FB  command=0x02FD
2026-11-13 20:20:45  IR           protocol=Gree AC  power=on  mode=cool  temp_C=24  fan=0  light=on  bytes=09 08 60 50 00 C0 00 70
```

The Gree field names (mode, temperature, …) are a best guess from the Gree layout; the bytes are exact.

## How it works

See [docs/NOTES.md](docs/NOTES.md): memory map, the bootloader's update protocol, every patch point,
and what was learned on the way (including why the firmware's debug prints must stay).

## Licence

MIT for the code in this repository. FNIRSI's firmware and tools are theirs and are not distributed here.

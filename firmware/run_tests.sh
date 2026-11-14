#!/usr/bin/env bash
# Build, then run the emulator tests (unicorn) against stock + patched image.
#   python3 -m venv .venv && .venv/bin/pip install unicorn     (once)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[[ -x $PY ]] || PY=python3
"$PY" -c 'import unicorn' 2>/dev/null || { echo "needs unicorn: python3 -m venv .venv && .venv/bin/pip install unicorn" >&2; exit 1; }
python3 "$ROOT/firmware/make_patch.py"
STOCK="$ROOT/fw/APP_LCR-P1-EN_V1.1.0_260324.BIN"; OUT="$ROOT/build/APP_LCR-P1-EN_V1.1.0u.BIN"
echo "== serial mirror"; "$PY" "$ROOT/firmware/test/emu_test.py" "$OUT"
echo "== IR";            "$PY" "$ROOT/firmware/test/ir_test.py" "$STOCK" "$OUT" "$ROOT/build/patch.elf"
echo "== commands";      "$PY" "$ROOT/firmware/test/control_test.py" "$OUT" "$ROOT/build/patch.elf"
echo "== screen";        "$PY" "$ROOT/firmware/test/screen_test.py" "$OUT" "$ROOT/build/patch.elf"
PYTHONPATH="$ROOT/app" python3 -m lcrp1 flash --check "$OUT"
echo "== pin-debug build"
PIN_DEBUG=1 python3 "$ROOT/firmware/make_patch.py" >/dev/null
"$PY" "$ROOT/firmware/test/pindump_test.py" "$ROOT/build/APP_LCR-P1-EN_V1.1.0u_pindebug.BIN" "$ROOT/build/patch.elf"
PYTHONPATH="$ROOT/app" python3 -m lcrp1 flash --check "$ROOT/build/APP_LCR-P1-EN_V1.1.0u_pindebug.BIN"
python3 "$ROOT/firmware/make_patch.py" >/dev/null     # leave build/ with the normal image + its patch.elf

#!/usr/bin/env bash
# LCR-P1 companion: per-user install (from this checkout). ./install.sh [--uninstall]
set -euo pipefail
APP_ID=io.github.bigtimer_dev.LcrP1
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
LIBDIR="$DATA/lcrp1-app"; BINDIR="$HOME/.local/bin"
DESKTOP="$DATA/applications/$APP_ID.desktop"; ICON="$DATA/icons/hicolor/scalable/apps/$APP_ID.svg"
if [[ ${1:-} == --uninstall ]]; then
  rm -rf "$LIBDIR" "$BINDIR/lcrp1" "$BINDIR/lcrp1-log" "$DESKTOP" "$ICON"
  command -v update-desktop-database >/dev/null && update-desktop-database -q "$DATA/applications" || true
  echo "LCR-P1 app removed."; exit 0
fi
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - <<'PY' || { echo "Needs Python 3 with PyGObject, GTK 4 and libadwaita >= 1.5" >&2; exit 1; }
import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
from gi.repository import Adw; assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5)
PY
mkdir -p "$LIBDIR" "$BINDIR" "$DATA/applications" "$(dirname "$ICON")"
rm -rf "$LIBDIR/lcrp1"; cp -r "$SRC/lcrp1" "$LIBDIR/"
find "$LIBDIR" -name __pycache__ -prune -exec rm -rf {} +
printf '#!/bin/sh\nexec env PYTHONPATH="%s" python3 -m lcrp1 "$@"\n' "$LIBDIR" > "$BINDIR/lcrp1"
printf '#!/bin/sh\nexec env PYTHONPATH="%s" python3 -m lcrp1 log "$@"\n' "$LIBDIR" > "$BINDIR/lcrp1-log"
chmod +x "$BINDIR/lcrp1" "$BINDIR/lcrp1-log"
sed "s|@BIN@|$BINDIR/lcrp1|g" "$SRC/data/$APP_ID.desktop" > "$DESKTOP"
cp "$SRC/data/$APP_ID.svg" "$ICON"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$DATA/applications" || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -t "$DATA/icons/hicolor" 2>/dev/null || true
groups | grep -qwE 'uucp|dialout' || echo "! add yourself to the uucp (Arch) / dialout group to open the meter's serial port"
echo "Installed: 'LCR-P1 Component Tester' in the app launcher, commands lcrp1 and lcrp1-log"

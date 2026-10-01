#!/usr/bin/env bash
# Set up the radar as a desktop widget on a Raspberry Pi (or any Linux desktop).
#
#   ./install.sh <lat> <lon> [range_nm] [size_px]
#   ./install.sh 60.3172 24.9633 25 360
#
# Installs pygame if needed, adds "Flight Radar" to the app menu, and starts it
# automatically when you log in. Run ./install.sh --uninstall to remove it.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTOSTART="$HOME/.config/autostart/flight-radar.desktop"
MENU="$HOME/.local/share/applications/flight-radar.desktop"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -f "$AUTOSTART" "$MENU"
  echo "Removed autostart and menu entries."
  exit 0
fi

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <lat> <lon> [range_nm] [size_px]   (or --uninstall)"
  echo "tip: right-click your location in Google Maps to copy the coordinates"
  exit 1
fi
LAT=$1 LON=$2 RANGE=${3:-25} SIZE=${4:-360}

if ! python3 -c "import pygame" 2>/dev/null; then
  echo "Installing pygame..."
  if command -v apt-get >/dev/null; then
    sudo apt-get install -y python3-pygame
  else
    python3 -m pip install --user pygame
  fi
fi

mkdir -p "$(dirname "$AUTOSTART")" "$(dirname "$MENU")"
for f in "$AUTOSTART" "$MENU"; do
  cat > "$f" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Flight Radar
Comment=Tiny round live flight radar
Exec=python3 "$DIR/radar.py" --lat $LAT --lon $LON --range $RANGE --size $SIZE
Icon=applications-science
Categories=Utility;
Terminal=false
DESKTOP
done

echo "Installed. Flight Radar is in your app menu and will start when you log in."
echo "Starting it now. Drag it where you want it; it remembers the spot."
nohup python3 "$DIR/radar.py" --lat "$LAT" --lon "$LON" --range "$RANGE" --size "$SIZE" >/dev/null 2>&1 &

#!/usr/bin/env bash
# One-shot setup for the Vantrue Thermal TS2 on Debian/Ubuntu.
#
#   ./setup_linux.sh          install udev rule + create venv + deps
#   ./setup_linux.sh --check  diagnose an existing install, change nothing
#
# Ubuntu 24.04 marks the system Python as externally managed (PEP 668), so
# this creates a virtualenv rather than fighting apt.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${TS2_VENV:-$HOME/.venv/ts2}"
RULE="99-vantrue-ts2.rules"
VIDPID="3474:45f2"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m !!\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }

check() {
    info "Camera present?"
    if lsusb 2>/dev/null | grep -qi "$VIDPID"; then
        ok "$(lsusb | grep -i "$VIDPID")"
    else
        warn "not found by lsusb -- plug the camera in"
    fi

    info "udev rule installed?"
    if [[ -f "/etc/udev/rules.d/$RULE" ]]; then ok "/etc/udev/rules.d/$RULE"
    else warn "missing -- run this script without --check"; fi

    info "Device node permissions?"
    local dev
    dev=$(lsusb 2>/dev/null | awk -v p="${VIDPID}" 'tolower($0) ~ tolower(p) {
              gsub(":","",$2); gsub(":","",$4); printf "/dev/bus/usb/%s/%s", $2, $4 }')
    if [[ -n "$dev" && -e "$dev" ]]; then
        ls -l "$dev"
        if [[ -r "$dev" && -w "$dev" ]]; then ok "readable and writable by $(id -un)"
        else warn "NOT accessible -- install the rule, then UNPLUG AND REPLUG"; fi
    fi

    info "No kernel driver should claim a vendor-class interface:"
    if command -v lsusb >/dev/null && lsusb -t 2>/dev/null | grep -q .; then
        lsusb -t 2>/dev/null | grep -i -B1 "3474" || echo "  (nothing bound - expected)"
    fi

    info "Fonts for OpenCV's bundled Qt?"
    if [[ -d /usr/share/fonts/truetype/dejavu ]]; then
        ok "/usr/share/fonts/truetype/dejavu (ts2_view.py sets QT_QPA_FONTDIR)"
    else
        warn "missing -- sudo apt install fonts-dejavu-core"
        warn "(harmless: causes only a QFontDatabase warning)"
    fi

    info "Python environment?"
    if [[ -x "$VENV/bin/python" ]]; then
        ok "$VENV"
        "$VENV/bin/python" - <<'PY' || warn "imports failed"
import cv2, numpy, usb.core
print(f"  numpy {numpy.__version__} | cv2 {cv2.__version__} | pyusb ok")
PY
    else
        warn "no venv at $VENV"
    fi
}

if [[ "${1:-}" == "--check" ]]; then check; exit 0; fi

info "Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y python3-venv libusb-1.0-0 libgl1 fonts-dejavu-core \
    libglib2.0-0t64 \
    || sudo apt-get install -y python3-venv libusb-1.0-0 libgl1 fonts-dejavu-core \
    libglib2.0-0

info "Installing udev rule"
sudo cp "$HERE/$RULE" /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
ok "rule installed"

info "Creating virtualenv at $VENV"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet numpy opencv-python pyusb
ok "dependencies installed"

cat <<EOF

$(printf '\033[1;32mDone.\033[0m')

  IMPORTANT: unplug and replug the camera now.  udev applies the rule when the
  device enumerates, so one already plugged in keeps its old permissions.

  Then run:
      $HERE/ts2_view.py

  (ts2_view.py finds $VENV by itself -- no 'activate' needed.)

  Verify without a window:
      $HERE/ts2_view.py --grab 3

  Diagnose:
      $HERE/setup_linux.sh --check
EOF

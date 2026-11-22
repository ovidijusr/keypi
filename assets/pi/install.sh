#!/bin/bash
# Install or update keypi on a Pi Zero / Zero W / Zero 2 W (Raspberry Pi OS).
# Run on the Pi from this folder:  sudo ./install.sh
# Reboots are only needed the first time (to enable the USB device-mode overlay).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo ./install.sh" >&2; exit 1; }
cd "$(dirname "$0")"

CFG=/boot/firmware/config.txt
[ -f "$CFG" ] || CFG=/boot/config.txt
NEED_REBOOT=0
# Exact-line match: stock config already has 'dtoverlay=dwc2,dr_mode=host' under [cm5].
if ! grep -qx 'dtoverlay=dwc2,dr_mode=peripheral' "$CFG"; then
  printf '\n[all]\ndtoverlay=dwc2,dr_mode=peripheral\n' >> "$CFG"
  NEED_REBOOT=1
fi

install -m 755 keypi-gadget.sh       /usr/local/bin/keypi-gadget.sh
install -m 755 keypi.py              /usr/local/bin/keypi
install -m 644 keypi-gadget.service  /etc/systemd/system/keypi-gadget.service
install -m 644 keypid.service        /etc/systemd/system/keypid.service
install -m 644 99-keypi-hidg.rules   /etc/udev/rules.d/99-keypi-hidg.rules
rm -f /usr/local/bin/keypi-type      # replaced by 'keypi type'

mkdir -p /etc/keypi
if [ ! -s /etc/keypi/token ]; then
  head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 32 > /etc/keypi/token
fi
chgrp plugdev /etc/keypi/token
chmod 640 /etc/keypi/token

udevadm control --reload
systemctl daemon-reload
systemctl enable keypi-gadget.service keypid.service

if [ "$NEED_REBOOT" = 1 ]; then
  echo "keypi installed. USB device mode was just enabled: reboot now (sudo reboot)."
else
  systemctl restart keypi-gadget.service
  systemctl restart keypid.service
  echo "keypi installed and running (API on port 8765, token in /etc/keypi/token)."
fi

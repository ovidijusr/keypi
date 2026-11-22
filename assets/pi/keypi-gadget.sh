#!/bin/bash
# keypi USB gadget: the Pi shows up on the computer it is plugged into as
#   hid.usb0  keyboard        (boot protocol, works in BIOS/UEFI)
#   hid.usb1  absolute mouse  (x/y 0..32767, used for exact clicks)
#   hid.usb2  relative mouse  (boot protocol, for BIOS screens that ignore absolute)
# Usage: keypi-gadget.sh        (re)create and attach
#        keypi-gadget.sh stop   detach and remove
set -euo pipefail

G=/sys/kernel/config/usb_gadget/keypi

teardown() {
  [ -d "$G" ] || return 0
  echo "" > "$G/UDC" 2>/dev/null || true
  for l in "$G"/configs/c.1/hid.*; do [ -L "$l" ] && rm "$l"; done
  rmdir "$G"/configs/c.1/strings/0x409 2>/dev/null || true
  rmdir "$G"/configs/c.1 2>/dev/null || true
  for f in "$G"/functions/*; do [ -d "$f" ] && rmdir "$f"; done
  rmdir "$G"/strings/0x409 2>/dev/null || true
  rmdir "$G"
}

if [ "${1:-}" = "stop" ]; then
  teardown
  exit 0
fi

modprobe libcomposite
teardown

UDC=$(ls /sys/class/udc 2>/dev/null | head -n1 || true)
if [ -z "$UDC" ]; then
  echo "keypi: no USB device controller. Is 'dtoverlay=dwc2,dr_mode=peripheral' in config.txt (then reboot)?" >&2
  exit 1
fi

mkdir -p "$G"
cd "$G"
echo 0x1d6b > idVendor    # Linux Foundation
echo 0x0104 > idProduct   # Multifunction Composite Gadget
echo 0x0200 > bcdDevice   # bumped when the function set changes (hosts cache descriptors)
echo 0x0200 > bcdUSB
mkdir -p strings/0x409
echo "keypi0002"           > strings/0x409/serialnumber
echo "keypi"               > strings/0x409/manufacturer
echo "keypi Keyboard+Mouse" > strings/0x409/product
mkdir -p configs/c.1/strings/0x409
echo "Keyboard+Mouse" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower

add_hid() {  # name protocol subclass report_length descriptor
  mkdir -p "functions/$1"
  echo "$2" > "functions/$1/protocol"
  echo "$3" > "functions/$1/subclass"
  echo "$4" > "functions/$1/report_length"
  printf "$5" > "functions/$1/report_desc"
  ln -s "functions/$1" configs/c.1/
}

# Keyboard: [mods, 0, k1..k6] (standard boot keyboard descriptor)
add_hid hid.usb0 1 1 8 '\x05\x01\x09\x06\xa1\x01\x05\x07\x19\xe0\x29\xe7\x15\x00\x25\x01\x75\x01\x95\x08\x81\x02\x95\x01\x75\x08\x81\x03\x95\x05\x75\x01\x05\x08\x19\x01\x29\x05\x91\x02\x95\x01\x75\x03\x91\x03\x95\x06\x75\x08\x15\x00\x25\x65\x05\x07\x19\x00\x29\x65\x81\x00\xc0'

# Absolute mouse: [buttons, x lo, x hi, y lo, y hi, wheel]; 5 buttons, x/y 0..32767, wheel -127..127
add_hid hid.usb1 2 0 6 '\x05\x01\x09\x02\xa1\x01\x09\x01\xa1\x00\x05\x09\x19\x01\x29\x05\x15\x00\x25\x01\x95\x05\x75\x01\x81\x02\x95\x01\x75\x03\x81\x03\x05\x01\x09\x30\x09\x31\x16\x00\x00\x26\xff\x7f\x75\x10\x95\x02\x81\x02\x09\x38\x15\x81\x25\x7f\x75\x08\x95\x01\x81\x06\xc0\xc0'

# Relative mouse: [buttons, dx, dy, wheel]
add_hid hid.usb2 2 1 4 '\x05\x01\x09\x02\xa1\x01\x09\x01\xa1\x00\x05\x09\x19\x01\x29\x05\x15\x00\x25\x01\x95\x05\x75\x01\x81\x02\x95\x01\x75\x03\x81\x03\x05\x01\x09\x30\x09\x31\x09\x38\x15\x81\x25\x7f\x75\x08\x95\x03\x81\x06\xc0\xc0'

echo "$UDC" > UDC
echo "keypi: keyboard + mice active on $UDC"

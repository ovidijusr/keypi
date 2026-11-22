# keypi setup, hardware and troubleshooting

## Contents
- Build a keypi from scratch
- Update the Pi side
- Power and cables
- Capture options
- Pi API
- Troubleshooting

## Build a keypi from scratch
1. Raspberry Pi Imager → device Raspberry Pi Zero (or Zero 2 W), OS **Raspberry Pi OS Lite** (32-bit for Zero/Zero W). Customisation: hostname, user + password, WiFi (2.4 GHz) + country, SSH with public key.
2. Optional, before ejecting: copy `assets/pi/*` to `bootfs/keypi/`, append `dtoverlay=dwc2,dr_mode=peripheral` to `config.txt`, and append to `user-data`:
   ```yaml
   runcmd:
   - [bash, /boot/firmware/keypi/install.sh]
   - [systemctl, start, keypi-gadget.service]
   ```
   The Pi then comes up as keyboard + mouse on first boot (5–10 min on a Zero W). `install-pi` is still needed afterwards to fetch the token.
3. Otherwise boot it, then from the controller: `kvm.py setup --pi <ip> --ssh-user pi` and `kvm.py install-pi [--sudo-pass-file F]`. First install enables the USB overlay and asks for a reboot (`ssh pi@<ip> sudo reboot`), then run `install-pi` again.
4. `kvm.py status`, pick the source, `kvm.py calibrate`.

## Update the Pi side
Edit `assets/pi/*`, then `kvm.py install-pi --sudo-pass-file F`. It copies the files, runs `install.sh` (idempotent: re-creates the gadget, restarts `keypid`), and re-reads the token. The target sees the USB device drop and return. If the function set changes, bump `bcdDevice`/serial in `keypi-gadget.sh` (Windows caches descriptors per VID/PID/bcdDevice).

Pi files: `/usr/local/bin/keypi-gadget.sh` (configfs gadget: hid.usb0 keyboard, hid.usb1 absolute mouse 0..32767, hid.usb2 relative boot mouse), `/usr/local/bin/keypi` (CLI + `serve`), `keypi-gadget.service`, `keypid.service` (DynamicUser + plugdev), `/etc/keypi/token` (root:plugdev 640), udev rule for `/dev/hidg*`. On the Pi directly: `keypi type -n "dir"`, `keypi key win+r`, `keypi click 16384 16384`.

## Power and cables
- Target cable goes in the Pi port labelled **USB** (middle), never PWR. Must be a data cable — "Unknown USB Device (Device Descriptor Request Failed)" during the Pi's boot is normal and proves data lines work.
- Powered only by the target: Pi reboots whenever the target does and needs 30–40 s, so it misses BIOS/boot keys. For boot-time work power it via PWR and put a USB power blocker on the target cable (avoid tying the two 5 V supplies).
- Models that can be a USB gadget: Zero / Zero W / Zero 2 W (micro-USB), Pi 4 / 5 (via USB-C, power-hungry), CM with carrier support. Not Pi 1B/2B/3B(+).

## Capture options
The Zero W has one USB data port, used by the gadget — a USB capture card cannot plug into it.
1. Capture card into the controller (simplest; target must be within HDMI reach).
2. Capture card into the Pi (OTG adapter) and a Pi Pico as the keyboard/mouse fed over UART — not implemented.
3. HDMI→CSI bridge (TC358743) on the Pi's camera connector — not implemented; Zero W manages snapshots, not smooth video.
Cheap MS2109 USB sticks send MJPEG; Mac recovery/firmware screens carry no HDCP. Apple Silicon laptops may show early boot only on the built-in display.

## Pi API
`GET /status` → `{"ok":true,"usb":"configured"}`; `POST /act` `{"actions":[...]}`, header `Authorization: Bearer <token>`. Actions run in order: `{"text":"..."}`, `{"key":"ctrl+c"}` or list, `{"hold":"cmd+r","seconds":10}`, `{"move":[x,y]}` (0..32767), `{"click":"left","at":[x,y],"count":2}`, `{"down":"left"}`, `{"up":"left"}`, `{"drag":[x1,y1,x2,y2]}`, `{"scroll":-3,"at":[x,y]}`, `{"rel":[dx,dy]}`, `{"sleep":0.5}`, `{"release":true}`. Error → HTTP 409 with `error` and `usb`.

## Troubleshooting
| Symptom | Check |
|---|---|
| `can't reach keypi` | Pi powered and on WiFi? `ssh pi@<ip> systemctl status keypid` |
| `target is not taking input` | target asleep/off, or USB state not `configured` |
| characters wrong | target layout not US, or Caps Lock on |
| clicks off by a lot | recalibrate; check the captured screen is the one the mouse lives on |
| calibration sees few points | someone moved the mouse, very busy screen, or pointer hidden (some apps/games hide it) |
| BIOS ignores the mouse | use keyboard, or the relative mouse (`{"rel":[dx,dy]}`) |
| Mac repeatedly resets the Pi during USB enumeration | Check cable/adapter/port and Pi UDC state plus `dmesg`. On 2026-11-08 Windows accepted this gadget and Mac Recovery did not; no USB fix is confirmed yet. For a network alternative, see [mac-recovery-ssh.md](mac-recovery-ssh.md). |

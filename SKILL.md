---
name: kvm
description: Drive another computer (often offline / air-gapped) through a "keypi" — a Raspberry Pi Zero W plugged into it as a USB keyboard + mouse — while seeing its screen through an HDMI capture card (or this machine's own screen when the keypi is plugged in here). Calibrate the mouse first, then execute - type commands, press key combos, click on what is in the screenshot, read the result. Use when the user says kvm, keypi, "type on the other computer", "run this on the offline/air-gapped machine", "control that PC/Mac through the Pi", "look at its screen", capture card, BIOS / boot menu keys, Mac recovery (Cmd+R) through the Pi, calibrate the mouse, or wants to set up / reinstall / fix the Pi keyboard-mouse gadget.
---

# kvm

```
controller (this machine)                    Pi Zero W "keypi"              target computer
kvm.py ──HTTP :8765 + token (WiFi)──▶ keypid ──/dev/hidg0..2──USB──▶ keyboard + abs mouse + rel mouse
   ▲                                                                       │ HDMI
   └──── ffmpeg ◀── capture card (or local screen when target = this PC) ◀─┘
```

All commands: `uv run <skill>/scripts/kvm.py <cmd>` (deps install automatically). Config + calibration live in `~/.config/kvm/config.json`, the latest screenshot in `~/.cache/kvm/shot.png`.

## 1. Check

`kvm.py status` → expect `API ok, USB configured` and a calibration line.
- `can't reach keypi` → Pi off / not on WiFi; find it (ping sweep, Raspberry Pi MAC `B8-27-EB`), `kvm.py setup --pi <ip>`.
- `USB not configured` → target off/asleep, or cable in the Pi's PWR port instead of USB.
- Not set up / new Pi → read [references/setup.md](references/setup.md).

## 2. Calibrate (first time, and whenever it may be stale)

`kvm.py calibrate` — moves the target's pointer over a grid (~45 s), finds it in the frames, fits mouse-units → frame-pixels, verifies.
- Calibrate when: no calibration; `shot` warns the frame size changed; the target's resolution/monitors/capture source changed; a click lands in the wrong place.
- Tell the user first: nobody may touch the target's mouse during it. A calm screen (desktop) helps; busy UIs just cost some sightings.
- Good result: most points seen, fit < 2 px, verify median < 8 px. "never saw the cursor" → `kvm.py shot` and check the right screen is captured and the pointer is visible.
- Windows maps the absolute mouse to the **primary monitor** only; macOS/Linux to the whole desktop. Calibration discovers this, nothing to configure.

## 3. Execute: look → act → look

1. `kvm.py shot --grid` → Read the PNG. Coordinates for every command are **pixels of that shot** (1280 wide by default). Use `--grid` when picking click targets; `shot --crop X,Y,W,H` to zoom into small text (read-only view; still click in shot coords).
2. Act:
   - `kvm.py click X Y [--button right] [--double]`, `move X Y`, `drag X1 Y1 X2 Y2`, `scroll N [--at X Y]`
   - `kvm.py type "text" [-n]` (-n = Enter after), `key ctrl+alt+del win+r f2 ...`, `hold cmd+r 15`
   - `kvm.py run "command"` = type + Enter + wait for the screen to settle + shot (terminal work)
   - `kvm.py wait [--timeout 20]` = wait for the screen to settle, then shot
   - `kvm.py release` if keys/buttons may be stuck
3. Look again before the next step. Never chain blind steps across a dialog, page load or focus change.

### Rules
- **Confirm focus before typing.** Text goes to whatever has focus on the target. Shot first; click into the intended field; then type. (A first test typed into a different, already-open Notepad tab.)
- **Target keyboard layout must be English (US)** — keys are sent as US key positions; other layouts garble symbols. Characters a US keyboard can't type (e.g. ą, ü) are skipped with a warning; use another route (e.g. a file) for them.
- Prefer keyboard over mouse for terminals and shortcuts; use the mouse for GUIs.
- Get authorization before destructive or irreversible actions on the target (erasing disks, reinstalling, firmware/BIOS changes, deleting data) and before typing credentials the user did not give for that purpose. Honor explicit authorization already given for the same action and target; ask if the scope changes.
- The target is often deliberately offline: never enable networking on it, and never switch the keypi to a USB network/storage gadget unless the user asks.
- When the keypi is plugged into **this** machine (source `screen`), every key and click lands on the user's own desktop: keep tests in a scratch file/window and say what will move.

### Keys and boot-time combos
Combo names: `ctrl alt shift win/cmd/gui altgr`, `enter esc tab space backspace delete insert home end pageup pagedown up down left right f1..f12 printscreen capslock menu`, any single US character (`ctrl+c`, `ctrl+shift+t`).
Mac: `cmd` = win, `option` = alt. Boot keys must be **held** while the machine starts: `hold cmd+r 20` (Intel Mac recovery), `hold option 15` (boot picker), PC: tap `f2`/`del`/`f12` repeatedly or `hold f12 10`. The keypi must be powered on its own (PWR port) to catch these, see [references/setup.md](references/setup.md). Apple Silicon Macs enter recovery only by holding the power button (or `sudo nvram "recovery-boot-mode=unused"` + reboot from a running macOS).

## Video sources
`kvm.py devices` lists them. `kvm.py setup --source dshow:"<name>"` (Windows), `v4l2:/dev/video0` (Linux), `avfoundation:<n>` (macOS), or `screen` (+ `--region X,Y,W,H`) when the target is this machine. `--video-size 1920x1080` forces a capture mode. Changing the source drops the calibration — calibrate again.
Cheap MS2109 sticks (Windows name "USB Video"): `--source "dshow:USB Video" --video-size 1920x1080 --pixel-format yuyv422` (uncompressed, 5 fps, sharp text; `mjpeg` = fast but blurrier). Leave `--color` unset: the default conversion reproduced colour bars exactly (checked 2026-11-08); purple-ish screens are the source's real colours.

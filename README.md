# keypi

Let an AI agent use a computer it has no software access to: a BIOS screen, an installer, a recovery mode, a machine kept off the network.

A Raspberry Pi Zero W plugs into the target and pretends to be a USB keyboard and mouse. An HDMI capture card shows the target's screen to the machine the agent runs on. The agent looks at a frame, decides, and sends keys and clicks.

```
controller (agent runs here)             Pi Zero W "keypi"            target computer
kvm.py ──HTTP :8765 + token (Wi-Fi)──▶ keypid ──/dev/hidg0..2──USB──▶ keyboard + mouse
   ▲                                                                     │ HDMI
   └──── ffmpeg ◀── capture card ◀───────────────────────────────────────┘
```

There is no OCR step. The agent reads the screenshot itself; `kvm.py shot` scales it to 1280 px wide and can draw a coordinate grid or cut a zoomed crop for small text.

## What you need

- Raspberry Pi Zero W (or Zero 2 W, Pi 4/5: any model that can be a USB gadget), with Raspberry Pi OS Lite
- a micro-USB **data** cable from the Pi's port marked USB to the target
- a USB HDMI capture stick plugged into the controller (the cheap MS2109 kind works)
- on the controller: [uv](https://docs.astral.sh/uv/) and ffmpeg

## Set up

Full steps, power and cable notes, the Pi's HTTP API and troubleshooting are in [references/setup.md](references/setup.md). In short:

```
uv run scripts/kvm.py setup --pi <pi address> --ssh-user pi
uv run scripts/kvm.py install-pi          # copies assets/pi to the Pi, makes it a keyboard + mouse
uv run scripts/kvm.py devices             # list capture devices
uv run scripts/kvm.py setup --source "dshow:USB Video" --video-size 1920x1080 --pixel-format yuyv422
uv run scripts/kvm.py calibrate           # about 45 s; nobody may touch the target's mouse
```

## Use

```
uv run scripts/kvm.py shot --grid         # look
uv run scripts/kvm.py click 640 360       # act, in pixels of that screenshot
uv run scripts/kvm.py type "dir" -n
uv run scripts/kvm.py key ctrl+alt+del
uv run scripts/kvm.py hold f12 10         # boot-time keys
uv run scripts/kvm.py run "ipconfig"      # type, Enter, wait for the screen to settle, screenshot
```

`SKILL.md` is the same thing written as instructions for a coding agent; drop this folder into your agent's skills directory to use it that way.

## How the mouse finds its way

The Pi sends an absolute mouse, with positions from 0 to 32767 on each axis. Where that lands in a screenshot depends on resolution, monitor layout and the capture source, so `calibrate` measures it: it moves the pointer over a 5 × 5 grid, finds the pointer in each frame by comparing it with the frames before and after, fits a straight line per axis, and checks five more points.

## Limits

- The target's keyboard layout must be English (US): keys are sent as key positions.
- Powered from the target alone, a Zero W needs 30 to 40 seconds to boot and misses BIOS keys. Power it separately for boot-time work.
- Some BIOS screens ignore an absolute mouse; a relative mouse is included as a fallback.
- Windows maps an absolute mouse to the primary monitor only.
- Mac Recovery did not accept the gadget over USB in testing (2026-11-08); no fix is confirmed. What worked there was SSH out of Recovery: [references/mac-recovery-ssh.md](references/mac-recovery-ssh.md).
- Not built: OCR, capture on the Pi itself, a test on a truly offline machine.

## Safety

The Pi's service accepts commands only with a random token created at install. It listens on your local network, so keep it on a network you trust. This types and clicks on a real machine with nothing to stop it: an agent using it should confirm what has focus before typing, and get a person's go-ahead before anything that erases, reinstalls or changes firmware.

## Licence

MIT

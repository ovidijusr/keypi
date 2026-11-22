#!/usr/bin/env python3
"""keypi: USB keyboard + mouse for the computer this Pi is plugged into.
Assumes the target uses an English (US) keyboard layout.

  keypi type [-n] TEXT           type text; -n presses Enter afterwards
  keypi key COMBO [COMBO...]     tap combos: ctrl+alt+del  win+r  f2  enter
  keypi hold COMBO SECONDS       hold a combo (e.g. cmd+r while a Mac starts)
  keypi move X Y                 absolute mouse position, 0..32767 on both axes
  keypi click [X Y] [-b right] [-c 2]
  keypi scroll N                 wheel steps, positive = up
  keypi rel DX DY                relative mouse move (BIOS screens)
  keypi release                  release all keys and buttons
  keypi serve [--port 8765]      HTTP API used by the kvm skill
"""
import argparse
import hmac
import json
import os
import select
import struct
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

GADGET = "/sys/kernel/config/usb_gadget/keypi"
TOKEN_FILE = "/etc/keypi/token"
SHIFT = 0x02

MODS = {
    "ctrl": 0x01, "control": 0x01, "lctrl": 0x01,
    "shift": 0x02, "lshift": 0x02,
    "alt": 0x04, "lalt": 0x04, "option": 0x04, "opt": 0x04,
    "win": 0x08, "gui": 0x08, "super": 0x08, "meta": 0x08, "cmd": 0x08, "command": 0x08,
    "rctrl": 0x10, "rshift": 0x20, "ralt": 0x40, "altgr": 0x40, "rwin": 0x80, "rcmd": 0x80,
}
KEYS = {
    "enter": 0x28, "return": 0x28, "esc": 0x29, "escape": 0x29,
    "backspace": 0x2A, "bs": 0x2A, "tab": 0x2B, "space": 0x2C,
    "capslock": 0x39, "printscreen": 0x46, "prtsc": 0x46,
    "scrolllock": 0x47, "pause": 0x48,
    "insert": 0x49, "ins": 0x49, "home": 0x4A, "pageup": 0x4B, "pgup": 0x4B,
    "delete": 0x4C, "del": 0x4C, "end": 0x4D, "pagedown": 0x4E, "pgdn": 0x4E,
    "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
    "menu": 0x65, "app": 0x65, "plus": 0x2E,
}
KEYS.update({f"f{i}": 0x3A + i - 1 for i in range(1, 13)})

CHARS = {}
for _i, _c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    CHARS[_c] = (0, 0x04 + _i)
    CHARS[_c.upper()] = (SHIFT, 0x04 + _i)
for _i, (_p, _s) in enumerate(zip("1234567890", "!@#$%^&*()")):
    CHARS[_p] = (0, 0x1E + _i)
    CHARS[_s] = (SHIFT, 0x1E + _i)
for _p, _s, _code in [
    ("-", "_", 0x2D), ("=", "+", 0x2E), ("[", "{", 0x2F), ("]", "}", 0x30),
    ("\\", "|", 0x31), (";", ":", 0x33), ("'", '"', 0x34), ("`", "~", 0x35),
    (",", "<", 0x36), (".", ">", 0x37), ("/", "?", 0x38),
]:
    CHARS[_p] = (0, _code)
    CHARS[_s] = (SHIFT, _code)
CHARS.update({" ": (0, 0x2C), "\t": (0, 0x2B), "\n": (0, 0x28)})

BUTTONS = {"left": 1, "right": 2, "middle": 4, "back": 8, "forward": 16}


class HidError(Exception):
    pass


def parse_combo(combo):
    """'ctrl+alt+del' -> (modifier bits, [keycodes])"""
    mod, codes = 0, []
    for part in combo.lower().split("+"):
        part = part.strip()
        if part in MODS:
            mod |= MODS[part]
        elif part in KEYS:
            codes.append(KEYS[part])
        elif len(part) == 1 and part in CHARS:
            m, c = CHARS[part]
            mod |= m
            codes.append(c)
        else:
            raise HidError(f"unknown key '{part}' in '{combo}'")
    if len(codes) > 6:
        raise HidError(f"at most 6 non-modifier keys per combo: '{combo}'")
    return mod, codes


def device_for(function):
    """/dev/hidgN that belongs to a gadget function (matched by device number)."""
    try:
        major, minor = open(f"{GADGET}/functions/{function}/dev").read().split(":")
    except OSError:
        raise HidError(f"USB gadget function {function} missing; is keypi-gadget.service running?")
    want = os.makedev(int(major), int(minor))
    for name in sorted(os.listdir("/dev")):
        if name.startswith("hidg") and os.stat(f"/dev/{name}").st_rdev == want:
            return f"/dev/{name}"
    raise HidError(f"no /dev/hidg* device for {function}")


class Hid:
    def __init__(self, function):
        self.function = function
        self.fd = None

    def write(self, report, timeout=2.0):
        if self.fd is None:
            self.fd = os.open(device_for(self.function), os.O_WRONLY)
        # The gadget takes a new report only after the host read the previous one.
        _, ready, _ = select.select([], [self.fd], [], timeout)
        if not ready:
            raise HidError("the target is not taking input (unplugged, asleep, or USB not set up on it)")
        try:
            os.write(self.fd, report)
        except OSError as e:
            self.close()
            raise HidError(f"write to {self.function} failed: {e}")

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class Keyboard:
    def __init__(self, delay=0.012):
        self.hid = Hid("hid.usb0")
        self.delay = delay

    def report(self, mod, codes, timeout=2.0):
        self.hid.write(bytes([mod, 0] + codes + [0] * (6 - len(codes))), timeout)
        time.sleep(self.delay)

    def tap(self, mod, codes):
        self.report(mod, codes)
        self.report(0, [])

    def combo(self, combo):
        self.tap(*parse_combo(combo))

    def type(self, text):
        skipped = []
        for ch in text:
            if ch == "\r":
                continue
            key = CHARS.get(ch)
            if key is None:
                skipped.append(ch)
                continue
            self.tap(key[0], [key[1]])
        return sorted(set(skipped))

    def hold(self, combo, seconds):
        """Keep re-sending the press so it survives the host resetting USB while it boots."""
        mod, codes = parse_combo(combo)
        press = bytes([mod, 0] + codes + [0] * (6 - len(codes)))
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                self.hid.write(press, timeout=0.2)
            except HidError:
                self.hid.close()
            time.sleep(0.1)
        self.report(0, [])

    def release(self):
        self.report(0, [])


class AbsMouse:
    def __init__(self):
        self.hid = Hid("hid.usb1")
        self.x = self.y = 16384
        self.buttons = 0

    def _send(self, wheel=0):
        self.hid.write(struct.pack("<BHHb", self.buttons, self.x, self.y, wheel))
        time.sleep(0.01)

    def move(self, x, y):
        self.x = max(0, min(32767, int(x)))
        self.y = max(0, min(32767, int(y)))
        self._send()

    def down(self, button="left"):
        self.buttons |= BUTTONS[button]
        self._send()

    def up(self, button="left"):
        self.buttons &= ~BUTTONS[button]
        self._send()

    def click(self, button="left", at=None, count=1):
        if at is not None:
            self.move(*at)
            time.sleep(0.05)
        for _ in range(count):
            self.down(button)
            time.sleep(0.03)
            self.up(button)
            time.sleep(0.06)

    def drag(self, x1, y1, x2, y2, button="left", steps=25):
        self.move(x1, y1)
        time.sleep(0.05)
        self.down(button)
        for i in range(1, steps + 1):
            self.move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
            time.sleep(0.012)
        self.up(button)

    def scroll(self, steps):
        for _ in range(abs(int(steps))):
            self._send(1 if steps > 0 else -1)
            time.sleep(0.02)

    def release(self):
        self.buttons = 0
        self._send()


class RelMouse:
    def __init__(self):
        self.hid = Hid("hid.usb2")

    def move(self, dx, dy, buttons=0):
        dx, dy = int(dx), int(dy)
        while dx or dy:
            sx, sy = max(-127, min(127, dx)), max(-127, min(127, dy))
            self.hid.write(struct.pack("<Bbbb", buttons, sx, sy, 0))
            dx, dy = dx - sx, dy - sy
            time.sleep(0.01)


class Keypi:
    def __init__(self):
        self.kbd = Keyboard()
        self.abs = AbsMouse()
        self.rel = RelMouse()

    def run(self, actions):
        """Run a list of action dicts in order; returns info for the caller."""
        skipped = []
        for i, a in enumerate(actions):
            try:
                if "text" in a:
                    skipped += self.kbd.type(a["text"])
                elif "key" in a:
                    for c in ([a["key"]] if isinstance(a["key"], str) else a["key"]):
                        self.kbd.combo(c)
                elif "hold" in a:
                    self.kbd.hold(a["hold"], float(a.get("seconds", 5)))
                elif "move" in a:
                    self.abs.move(*a["move"])
                elif "click" in a:
                    self.abs.click(a["click"] or "left", a.get("at"), int(a.get("count", 1)))
                elif "down" in a:
                    self.abs.down(a["down"])
                elif "up" in a:
                    self.abs.up(a["up"])
                elif "drag" in a:
                    self.abs.drag(*a["drag"], button=a.get("button", "left"))
                elif "scroll" in a:
                    if a.get("at"):
                        self.abs.move(*a["at"])
                    self.abs.scroll(a["scroll"])
                elif "rel" in a:
                    self.rel.move(*a["rel"])
                elif "sleep" in a:
                    time.sleep(float(a["sleep"]))
                elif "release" in a:
                    self.kbd.release()
                    self.abs.release()
                else:
                    raise HidError(f"unknown action {a}")
            except (HidError, KeyError, TypeError, ValueError) as e:
                raise HidError(f"action {i + 1} {json.dumps(a)}: {e}")
        return {"skipped": sorted(set(skipped))}


def udc_state():
    try:
        udc = os.listdir("/sys/class/udc")[0]
        return open(f"/sys/class/udc/{udc}/state").read().strip()
    except (OSError, IndexError):
        return "no-udc"


def serve(port):
    try:
        token = open(TOKEN_FILE).read().strip()
    except OSError as e:
        sys.exit(f"keypi: can't read {TOKEN_FILE}: {e}")
    if not token:
        sys.exit(f"keypi: {TOKEN_FILE} is empty")
    kp = Keypi()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            if hmac.compare_digest(got, token):
                return True
            self._reply(401, {"ok": False, "error": "bad token"})
            return False

        def do_GET(self):
            if not self._authed():
                return
            if self.path == "/status":
                self._reply(200, {"ok": True, "usb": udc_state()})
            else:
                self._reply(404, {"ok": False, "error": "not found"})

        def do_POST(self):
            if not self._authed():
                return
            if self.path != "/act":
                return self._reply(404, {"ok": False, "error": "not found"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                info = kp.run(body.get("actions", []))
                self._reply(200, {"ok": True, **info})
            except HidError as e:
                self._reply(409, {"ok": False, "error": str(e), "usb": udc_state()})
            except ValueError as e:
                self._reply(400, {"ok": False, "error": f"bad request: {e}"})

        def log_message(self, *args):
            pass

    HTTPServer.allow_reuse_address = True
    srv = HTTPServer(("0.0.0.0", port), Handler)
    print(f"keypi: serving on :{port}", flush=True)
    srv.serve_forever()


def main():
    p = argparse.ArgumentParser(prog="keypi", description=__doc__.split("\n\n")[0],
                                epilog=__doc__.split("\n\n", 1)[1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("type"); s.add_argument("text", nargs="?"); s.add_argument("-n", "--enter", action="store_true")
    s = sub.add_parser("key"); s.add_argument("combos", nargs="+")
    s = sub.add_parser("hold"); s.add_argument("combo"); s.add_argument("seconds", type=float)
    s = sub.add_parser("move"); s.add_argument("x", type=int); s.add_argument("y", type=int)
    s = sub.add_parser("click"); s.add_argument("xy", nargs="*", type=int)
    s.add_argument("-b", "--button", default="left", choices=BUTTONS); s.add_argument("-c", "--count", type=int, default=1)
    s = sub.add_parser("scroll"); s.add_argument("steps", type=int)
    s = sub.add_parser("rel"); s.add_argument("dx", type=int); s.add_argument("dy", type=int)
    sub.add_parser("release")
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8765)
    a = p.parse_args()

    if a.cmd == "serve":
        return serve(a.port)
    actions = {
        "type": lambda: [{"text": a.text if a.text is not None else sys.stdin.read().rstrip("\r\n")}]
                        + ([{"key": "enter"}] if a.enter else []),
        "key": lambda: [{"key": a.combos}],
        "hold": lambda: [{"hold": a.combo, "seconds": a.seconds}],
        "move": lambda: [{"move": [a.x, a.y]}],
        "click": lambda: [{"click": a.button, "at": a.xy[:2] if len(a.xy) >= 2 else None, "count": a.count}],
        "scroll": lambda: [{"scroll": a.steps}],
        "rel": lambda: [{"rel": [a.dx, a.dy]}],
        "release": lambda: [{"release": True}],
    }[a.cmd]()
    try:
        info = Keypi().run(actions)
    except HidError as e:
        sys.exit(f"keypi: {e}")
    if info["skipped"]:
        print("keypi: skipped characters a US keyboard can't type: " + " ".join(info["skipped"]), file=sys.stderr)


if __name__ == "__main__":
    main()

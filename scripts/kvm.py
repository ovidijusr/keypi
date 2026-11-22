#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "pillow"]
# ///
"""kvm: drive a computer through a keypi (Pi Zero USB keyboard + mouse) and a video source.

Run with:  uv run kvm.py <command> ...

setup / status
  setup --pi HOST [--source SRC] [--region X,Y,W,H]   save settings (~/.config/kvm/config.json)
  install-pi [--host H] [--user pi]                    push assets/pi to the Pi, install, fetch token
  devices                                              list capture devices for --source
  status                                               Pi reachable? USB attached? calibrated?
see
  shot [--grid] [--width 1280] [--crop X,Y,W,H]        screenshot -> PNG path (coords = shot pixels)
  wait [--timeout 20]                                  wait until the screen stops changing, then shot
calibrate
  calibrate [--grid 5]                                 map mouse coordinates to screen pixels
act (X Y are pixels in the last full shot)
  click X Y [--button right] [--double]   move X Y   drag X1 Y1 X2 Y2   scroll N [--at X Y]
  type TEXT [-n]   key COMBO...   hold COMBO SECONDS   run COMMAND [--timeout 30]   release
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import warnings
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

CONFIG = Path.home() / ".config" / "kvm" / "config.json"
CACHE = Path.home() / ".cache" / "kvm"
STATE = CACHE / "state.json"
SKILL_DIR = Path(__file__).resolve().parent.parent
HID_MAX = 32767
warnings.filterwarnings("ignore", message=".*poorly conditioned.*")


# ---------------------------------------------------------------- config / state

def load(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def cfg():
    c = load(CONFIG)
    if not c.get("pi"):
        sys.exit(f"kvm: not set up. Run: kvm.py setup --pi <host>   (config: {CONFIG})")
    return c


# ---------------------------------------------------------------- Pi API

class Pi:
    def __init__(self, c):
        self.base = f"http://{c['pi']}:{c.get('port', 8765)}"
        self.token = c.get("token", "")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, method, path, body=None, timeout=30):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read())
            except ValueError:
                return {"ok": False, "error": f"HTTP {e.code}"}
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            return {"ok": False, "error": f"can't reach keypi at {self.base}: {getattr(e, 'reason', e)}"}

    def act(self, actions, timeout=30):
        r = self.call("POST", "/act", {"actions": actions}, timeout)
        if not r.get("ok"):
            sys.exit(f"kvm: {r.get('error')}" + (f" (usb: {r['usb']})" if r.get("usb") else ""))
        if r.get("skipped"):
            print("kvm: skipped characters a US keyboard can't type: " + " ".join(r["skipped"]), file=sys.stderr)
        return r


# ---------------------------------------------------------------- video

def ffmpeg_input(c):
    src = c.get("source", "screen")
    system = platform.system()
    size = ["-video_size", c["video_size"]] if c.get("video_size") else []
    if src == "screen":
        if system == "Windows":
            args = ["-f", "gdigrab", "-draw_mouse", "1"]
            if c.get("region"):
                x, y, w, h = c["region"]
                args += ["-offset_x", str(x), "-offset_y", str(y), "-video_size", f"{w}x{h}"]
            return args + ["-i", "desktop"], 0
        if system == "Darwin":
            return ["-f", "avfoundation", "-capture_cursor", "1", "-i", f"{c.get('screen_index', 1)}:none"], 0
        return ["-f", "x11grab", "-draw_mouse", "1", "-i", os.environ.get("DISPLAY", ":0")], 0
    kind, _, dev = src.partition(":")
    if c.get("pixel_format"):  # e.g. yuyv422 (sharp, slow) or mjpeg (fast, JPEG artefacts)
        size += ["-vcodec", "mjpeg"] if c["pixel_format"] == "mjpeg" else ["-pixel_format", c["pixel_format"]]
    if kind == "dshow":
        return ["-f", "dshow", "-rtbufsize", "64M"] + size + ["-i", f"video={dev}"], 5
    if kind == "v4l2":
        return ["-f", "v4l2"] + size + ["-i", dev], 5
    if kind == "avfoundation":
        return ["-f", "avfoundation"] + size + ["-i", f"{dev}:none"], 5
    sys.exit(f"kvm: unknown source '{src}' (screen, dshow:NAME, v4l2:/dev/videoN, avfoundation:N)")


def grab(c):
    """One frame from the video source as an RGB uint8 array."""
    inp, skip = ffmpeg_input(c)
    filters = [f"select=gte(n\\,{skip})"] if skip else []
    if c.get("color") and c.get("pixel_format") not in (None, "mjpeg"):
        # override how raw YUV is read, "matrix:range". Leave unset normally: ffmpeg's default (bt601:tv)
        # reproduced 100% colour bars exactly on an MS2109 "USB Video" stick; bt709:pc was clearly wrong.
        matrix, _, rng = c["color"].partition(":")
        filters.append(f"scale=in_color_matrix={matrix}:in_range={rng or 'tv'}:out_range=pc")
    vf = ["-vf", ",".join(filters)] if filters else []
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", *inp, *vf,
           "-frames:v", "1", "-c:v", "bmp", "-f", "image2pipe", "-"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=30)
    except FileNotFoundError:
        sys.exit("kvm: ffmpeg not found on PATH")
    except subprocess.TimeoutExpired:
        sys.exit("kvm: video source timed out (is the capture card plugged in / in use by another app?)")
    if out.returncode or not out.stdout:
        sys.exit("kvm: grabbing a frame failed: " + out.stderr.decode(errors="replace").strip()[-400:])
    return np.asarray(Image.open(BytesIO(out.stdout)).convert("RGB"))


def changed(a, b, thr=40):
    return np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2) > thr


# ---------------------------------------------------------------- shots

def font(size):
    for name in ("arial.ttf", "DejaVuSans.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def draw_grid(img, step=100):
    img = img.convert("RGBA")
    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    f = font(12)
    w, h = img.size
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=(255, 0, 255, 90), width=1)
        for y in range(0, h, step * 2):
            d.text((x + 2, y + 2), f"{x},{y}", fill=(255, 0, 255, 230), font=f)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=(255, 0, 255, 90), width=1)
    return Image.alpha_composite(img, over).convert("RGB")


def take_shot(c, width=1280, grid=False, crop=None, out=None, frame=None):
    frame = grab(c) if frame is None else frame
    fh, fw = frame.shape[:2]
    img = Image.fromarray(frame)
    scale = min(1.0, width / fw) if width else 1.0
    CACHE.mkdir(parents=True, exist_ok=True)
    if crop:
        st = load(STATE)
        s = st.get("scale", scale)
        x, y, w, h = crop
        box = tuple(int(round(v / s)) for v in (x, y, x + w, y + h))
        zoom = max(1.0, min(4.0, 1200 / max(1, box[2] - box[0])))
        part = img.crop(box)
        part = part.resize((int(part.width * zoom), int(part.height * zoom)), Image.LANCZOS)
        path = Path(out) if out else CACHE / "crop.png"
        part.save(path)
        k = zoom / s  # crop pixels per shot pixel
        print(f"{path}\ncrop of shot area {x},{y} {w}x{h}, {k:.2f} crop px per shot px. "
              f"Crop pixel (u,v) = shot ({x} + u/{k:.2f}, {y} + v/{k:.2f}). Click with shot coords.")
        return path
    if scale < 1:
        img = img.resize((round(fw * scale), round(fh * scale)), Image.LANCZOS)
    if grid:
        img = draw_grid(img)
    path = Path(out) if out else CACHE / "shot.png"
    img.save(path)
    st = load(STATE)
    st.update({"scale": scale, "frame": [fw, fh], "shot": [img.width, img.height], "time": time.time()})
    save(STATE, st)
    cal = c.get("calibration")
    note = ""
    if not cal:
        note = "  (mouse not calibrated: run calibrate)"
    elif cal.get("frame") != [fw, fh]:
        note = f"  (WARNING: frame size changed since calibration {cal.get('frame')}: recalibrate)"
    print(f"{path}\nshot {img.width}x{img.height} (frame {fw}x{fh}, scale {scale:.4f}){note}")
    return path


def wait_settle(c, timeout=20.0, quiet_frac=0.0005, needed=2):
    """Grab until the picture stops changing (blinking carets are below the threshold)."""
    start, prev, calm, frame = time.monotonic(), None, 0, None
    while time.monotonic() - start < timeout:
        frame = grab(c)
        small = frame[::4, ::4]
        if prev is not None:
            frac = changed(small, prev, 24).mean()
            calm = calm + 1 if frac < quiet_frac else 0
            if calm >= needed:
                return frame, True
        prev = small
        time.sleep(0.3)
    return frame, False


# ---------------------------------------------------------------- calibration

def components(mask, limit=150_000):
    """Connected components (8-neighbour) of a sparse boolean mask: list of (count, x0, y0, x1, y1)."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0 or len(xs) > limit:
        return []
    pts = set(zip(xs.tolist(), ys.tolist()))
    comps = []
    while pts:
        seed = pts.pop()
        stack, n = [seed], 0
        x0 = x1 = seed[0]
        y0 = y1 = seed[1]
        while stack:
            x, y = stack.pop()
            n += 1
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    q = (x + dx, y + dy)
                    if q in pts:
                        pts.remove(q)
                        stack.append(q)
        comps.append((n, x0, y0, x1, y1))
    return comps


def find_cursor(frame, before, after):
    """The cursor in `frame`: pixels that differ from the frames just before and after
    (cursor elsewhere in both) at places where those two agree."""
    m = changed(frame, before) & changed(frame, after) & ~changed(before, after)
    cands = [cp for cp in components(m)
             if 8 <= cp[0] <= 8000 and cp[3] - cp[1] < 160 and cp[4] - cp[2] < 160]
    if not cands:
        return None
    arrowish = [cp for cp in cands if (cp[4] - cp[2]) >= 0.8 * (cp[3] - cp[1])] or cands
    best = max(arrowish, key=lambda cp: cp[0])
    return best[1], best[2]  # top-left = arrow tip (hotspot)


def fit(points):
    """points: [(hx, hy, px, py)] -> (ax, bx, ay, by, residuals, keep) with outlier rejection."""
    P = np.array(points, float)
    keep = np.ones(len(P), bool)
    for _ in range(4):
        if keep.sum() < 3:
            return None
        ax, bx = np.polyfit(P[keep, 0], P[keep, 2], 1)
        ay, by = np.polyfit(P[keep, 1], P[keep, 3], 1)
        res = np.hypot(ax * P[:, 0] + bx - P[:, 2], ay * P[:, 1] + by - P[:, 3])
        thr = max(6.0, 3 * float(np.median(res[keep])))
        new = res <= thr
        if (new == keep).all():
            break
        keep = new
    if keep.sum() < 4 or len(set(P[keep, 0])) < 2 or len(set(P[keep, 1])) < 2:
        return None
    return ax, bx, ay, by, res, keep


def calibrate(c, grid=5):
    pi = Pi(c)
    settle = float(c.get("settle", 0.15 if c.get("source", "screen") == "screen" else 0.4))

    def at(hx, hy):
        pi.act([{"move": [int(hx), int(hy)]}])
        time.sleep(settle)
        return grab(c)

    def observe(points):
        """Visit points in order (bracketed by far-away parks); each frame is compared with
        its neighbours, which are only a second apart, so other screen activity rarely matters.
        Returns [(hx, hy, px, py)] for points where the cursor was seen."""
        path = [(HID_MAX * 0.02, HID_MAX * 0.02)] + list(points) + [(HID_MAX * 0.98, HID_MAX * 0.98)]
        frames, found = [], []
        for i, (hx, hy) in enumerate(path):
            frames.append(at(hx, hy))
            if len(frames) == 3:
                hs = find_cursor(frames[1], frames[0], frames[2])
                if hs:
                    found.append((*path[i - 1], *hs))
                frames.pop(0)
        return found

    def serpentine(x_lo, x_hi, y_lo, y_hi, n):
        pts = []
        for r, hy in enumerate(np.linspace(y_lo, y_hi, n)):
            row = [(hx, hy) for hx in np.linspace(x_lo, x_hi, n)]
            pts += row if r % 2 == 0 else row[::-1]
        return pts

    print("calibrate: moving the target's mouse; don't touch it for ~30-60 s")
    fh, fw = grab(c).shape[:2]

    def sweep(x_lo, x_hi, y_lo, y_hi, n):
        return observe(serpentine(x_lo, x_hi, y_lo, y_hi, n))

    pts = sweep(HID_MAX * 0.08, HID_MAX * 0.92, HID_MAX * 0.08, HID_MAX * 0.92, grid)
    print(f"calibrate: pass 1 saw the cursor at {len(pts)}/{grid * grid} points")
    if len(pts) < 2 * grid:  # visible area is small (e.g. capture shows one of several monitors)
        if not pts:
            sys.exit("kvm: never saw the cursor. Is the right screen captured, the mouse visible, "
                     "and the keypi USB plugged into this target? Check with: kvm.py shot")
        step = HID_MAX * 0.84 / (grid - 1)
        hx = [p[0] for p in pts]
        hy = [p[1] for p in pts]
        box = (max(0, min(hx) - step * 0.8), min(HID_MAX, max(hx) + step * 0.8),
               max(0, min(hy) - step * 0.8), min(HID_MAX, max(hy) + step * 0.8))
        pts += sweep(*box, grid)
        print(f"calibrate: pass 2 (zoomed in) -> {len(pts)} points total")

    f = fit(pts)
    if not f:
        sys.exit("kvm: calibration failed: not enough consistent cursor sightings. "
                 "Show a calm screen (e.g. the desktop) and try again.")
    ax, bx, ay, by, res, keep = f

    # verify: aim at known pixels, see where the cursor lands
    targets = {}
    for tx, ty in [(0.3, 0.3), (0.7, 0.35), (0.5, 0.6), (0.25, 0.75), (0.8, 0.8)]:
        px, py = tx * fw, ty * fh
        hx, hy = (px - bx) / ax, (py - by) / ay
        if 0 <= hx <= HID_MAX and 0 <= hy <= HID_MAX:
            targets[(hx, hy)] = (px, py)
    errs = [float(np.hypot(sx - targets[(hx, hy)][0], sy - targets[(hx, hy)][1]))
            for hx, hy, sx, sy in observe(list(targets))]
    pi.act([{"move": [int((fw / 2 - bx) / ax), int((fh / 2 - by) / ay)]}])

    reach = [bx, by, ax * HID_MAX + bx, ay * HID_MAX + by]
    cal = {"frame": [fw, fh], "ax": ax, "bx": bx, "ay": ay, "by": by,
           "points": int(keep.sum()), "fit_px": round(float(np.median(res[keep])), 2),
           "verify_px": round(float(np.median(errs)), 1) if errs else None,
           "mouse_area_px": [round(v) for v in reach], "source": c.get("source", "screen"),
           "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    c["calibration"] = cal
    save(CONFIG, c)
    print(f"calibrate: OK. {cal['points']} points, median fit error {cal['fit_px']} px, "
          f"verify median error {cal['verify_px']} px ({len(errs)} checks)")
    print(f"calibrate: mouse range covers frame pixels x {reach[0]:.0f}..{reach[2]:.0f}, "
          f"y {reach[1]:.0f}..{reach[3]:.0f} (frame {fw}x{fh})")
    if errs and np.median(errs) > 8:
        print("calibrate: WARNING verify error is high; recalibrate on a calm screen")


def to_hid(c, x, y):
    cal = c.get("calibration")
    if not cal:
        sys.exit("kvm: mouse not calibrated. Run: kvm.py calibrate")
    st = load(STATE)
    s = st.get("scale") or min(1.0, 1280 / cal["frame"][0])
    if st.get("frame") and st["frame"] != cal["frame"]:
        sys.exit(f"kvm: screen size changed ({st['frame']} vs calibrated {cal['frame']}). Run: kvm.py calibrate")
    px, py = x / s, y / s
    hx, hy = (px - cal["bx"]) / cal["ax"], (py - cal["by"]) / cal["ay"]
    if not (-50 <= hx <= HID_MAX + 50 and -50 <= hy <= HID_MAX + 50):
        sys.exit(f"kvm: ({x},{y}) is outside the area the mouse can reach")
    return [max(0, min(HID_MAX, round(hx))), max(0, min(HID_MAX, round(hy)))]


# ---------------------------------------------------------------- commands

def cmd_setup(a):
    c = load(CONFIG)
    for k in ("pi", "port", "token", "source", "ssh_user", "video_size", "pixel_format", "color"):
        v = getattr(a, k)
        if v is not None:
            c[k] = v
    if a.region is not None:
        c["region"] = [int(v) for v in a.region.split(",")] if a.region else None
    if c.get("source") != load(CONFIG).get("source") or a.region is not None:
        c.pop("calibration", None)
    save(CONFIG, c)
    print(json.dumps({k: ("***" if k == "token" else v) for k, v in c.items() if k != "calibration"}, indent=2))


def cmd_install_pi(a):
    c = load(CONFIG)
    host = a.host or c.get("pi")
    user = a.user or c.get("ssh_user", "pi")
    if not host:
        sys.exit("kvm: give --host")
    src = SKILL_DIR / "assets" / "pi"
    target = f"{user}@{host}"
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", target]
    tmp = Path(tempfile.mkdtemp())
    for f in src.iterdir():  # normalise line endings in case the repo was checked out with CRLF
        (tmp / f.name).write_bytes(f.read_bytes().replace(b"\r\n", b"\n"))
    subprocess.run(ssh + ["rm -rf ~/keypi-install && mkdir -p ~/keypi-install"], check=True)
    subprocess.run(["scp", "-q", "-o", "BatchMode=yes", *[str(p) for p in tmp.iterdir()],
                    f"{target}:keypi-install/"], check=True)
    shutil.rmtree(tmp, ignore_errors=True)
    # sudo: passwordless if the Pi allows it, else the password from --sudo-pass-file via stdin
    pw = Path(a.sudo_pass_file).read_text().strip() + "\n" if a.sudo_pass_file else ""
    sudo = "sudo -S -p ''" if pw else "sudo -n"

    def remote(cmd, capture=False):
        r = subprocess.run(ssh + [cmd], input=pw, text=True, capture_output=capture)
        if r.returncode:
            err = (r.stderr or "").strip()
            hint = " (sudo needs a password: pass --sudo-pass-file)" if "password" in err else ""
            sys.exit(f"kvm: '{cmd}' failed on the Pi{hint}\n{err}")
        return r.stdout

    remote(f"cd ~/keypi-install && {sudo} bash ./install.sh")
    token = remote(f"{sudo} cat /etc/keypi/token", capture=True).strip()
    c.update({"pi": host, "ssh_user": user, "token": token})
    c.setdefault("port", 8765)
    save(CONFIG, c)
    print(f"kvm: keypi installed on {host}; token saved to {CONFIG}")


def cmd_devices(a):
    system = platform.system()
    if system == "Windows":
        r = subprocess.run(["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                           capture_output=True, text=True, errors="replace")
        names = [ln.split('"')[1] for ln in r.stderr.splitlines() if '"' in ln and "(video)" in ln]
        print("\n".join(f"dshow:{n}" for n in names) or "no video capture devices found")
    elif system == "Darwin":
        r = subprocess.run(["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                           capture_output=True, text=True, errors="replace")
        print(r.stderr)
    else:
        print("\n".join(f"v4l2:{p}" for p in sorted(map(str, Path("/dev").glob("video*")))) or "none")
    print("\nlocal screen (when the keypi is plugged into this machine): screen")


def cmd_status(a):
    c = cfg()
    r = Pi(c).call("GET", "/status", timeout=8)
    print(f"keypi {c['pi']}: " + (f"API ok, USB {r.get('usb')}" if r.get("ok") else r.get("error")))
    if r.get("ok") and r.get("usb") != "configured":
        print("  USB is not 'configured': the target is off/asleep, or the cable/port is wrong")
    print(f"video source: {c.get('source', 'screen')}" + (f" region {c['region']}" if c.get("region") else ""))
    cal = c.get("calibration")
    print("calibration: " + (f"{cal['frame'][0]}x{cal['frame'][1]}, verify {cal.get('verify_px', cal.get('verify_max_px'))} px, "
                             f"{cal['time']}" if cal else "none (run calibrate)"))


def parse_box(s):
    return [int(v) for v in s.split(",")] if s else None


def main():
    p = argparse.ArgumentParser(prog="kvm.py", description=__doc__.split("\n\n")[0],
                                epilog=__doc__.split("\n\n", 1)[1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--pi"); s.add_argument("--port", type=int); s.add_argument("--token")
    s.add_argument("--source"); s.add_argument("--region", help="X,Y,W,H of the local screen ('' = all)")
    s.add_argument("--ssh-user", dest="ssh_user"); s.add_argument("--video-size", dest="video_size")
    s.add_argument("--pixel-format", dest="pixel_format", help="capture mode: yuyv422 (sharp) or mjpeg (fast)")
    s.add_argument("--color", help="raw YUV interpretation matrix:range, e.g. bt709:pc")
    s = sub.add_parser("install-pi"); s.add_argument("--host"); s.add_argument("--user")
    s.add_argument("--sudo-pass-file", help="file holding the Pi user's password, if sudo asks for one")
    sub.add_parser("devices")
    sub.add_parser("status")
    s = sub.add_parser("shot")
    s.add_argument("--grid", action="store_true"); s.add_argument("--width", type=int, default=1280)
    s.add_argument("--crop", type=parse_box, help="X,Y,W,H in shot pixels: zoomed view for reading")
    s.add_argument("--out")
    s = sub.add_parser("wait"); s.add_argument("--timeout", type=float, default=20)
    s.add_argument("--grid", action="store_true")
    s = sub.add_parser("calibrate"); s.add_argument("--grid", type=int, default=5)
    for name in ("click", "move"):
        s = sub.add_parser(name); s.add_argument("x", type=float); s.add_argument("y", type=float)
        if name == "click":
            s.add_argument("--button", default="left", choices=["left", "right", "middle"])
            s.add_argument("--double", action="store_true")
    s = sub.add_parser("drag")
    for n in ("x1", "y1", "x2", "y2"):
        s.add_argument(n, type=float)
    s = sub.add_parser("scroll"); s.add_argument("steps", type=int); s.add_argument("--at", nargs=2, type=float)
    s = sub.add_parser("type"); s.add_argument("text"); s.add_argument("-n", "--enter", action="store_true")
    s = sub.add_parser("key"); s.add_argument("combos", nargs="+")
    s = sub.add_parser("hold"); s.add_argument("combo"); s.add_argument("seconds", type=float)
    s = sub.add_parser("run"); s.add_argument("command"); s.add_argument("--timeout", type=float, default=30)
    sub.add_parser("release")
    a = p.parse_args()

    if a.cmd == "setup":
        return cmd_setup(a)
    if a.cmd == "install-pi":
        return cmd_install_pi(a)
    if a.cmd == "devices":
        return cmd_devices(a)
    c = cfg()
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "shot":
        return take_shot(c, a.width, a.grid, a.crop, a.out)
    if a.cmd == "wait":
        frame, calm = wait_settle(c, a.timeout)
        print("screen settled" if calm else f"screen still changing after {a.timeout:.0f}s")
        return take_shot(c, grid=a.grid, frame=frame)
    if a.cmd == "calibrate":
        return calibrate(c, a.grid)

    pi = Pi(c)
    if a.cmd == "click":
        pi.act([{"click": a.button, "at": to_hid(c, a.x, a.y), "count": 2 if a.double else 1}])
    elif a.cmd == "move":
        pi.act([{"move": to_hid(c, a.x, a.y)}])
    elif a.cmd == "drag":
        pi.act([{"drag": to_hid(c, a.x1, a.y1) + to_hid(c, a.x2, a.y2)}])
    elif a.cmd == "scroll":
        pi.act([{"scroll": a.steps, "at": to_hid(c, *a.at) if a.at else None}])
    elif a.cmd == "type":
        pi.act([{"text": a.text}] + ([{"key": "enter"}] if a.enter else []), timeout=15 + len(a.text) * 0.05)
    elif a.cmd == "key":
        pi.act([{"key": a.combos}])
    elif a.cmd == "hold":
        pi.act([{"hold": a.combo, "seconds": a.seconds}], timeout=a.seconds + 15)
    elif a.cmd == "release":
        pi.act([{"release": True}])
    elif a.cmd == "run":
        pi.act([{"text": a.command}, {"key": "enter"}], timeout=15 + len(a.command) * 0.05)
        time.sleep(0.5)
        frame, calm = wait_settle(c, a.timeout)
        print("screen settled" if calm else f"screen still changing after {a.timeout:.0f}s")
        return take_shot(c, frame=frame)
    print("ok")


if __name__ == "__main__":
    main()

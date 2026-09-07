#!/usr/bin/env python3
"""Live viewer / grabber for the Vantrue Thermal TS2.

    ./ts2_view.py                   live window
    ./ts2_view.py --grab 10         save 10 frames as PNG + .npy and exit

In the live window, hovering shows the temperature under the pointer (it
disappears when the pointer leaves the image).  Keys:
    q / Esc  quit          p  cycle palette
    f        fire shutter  s  save frame (PNG + .npy)
    left-click             pin a spot reading (click again to clear)

If the dependencies are missing from the interpreter that started this script,
it re-executes itself with a virtualenv's python -- see _reexec_in_venv().
"""
import argparse
import os
import sys
import time

# Virtualenvs to fall back on, in order.  $TS2_VENV wins; then a venv sitting
# next to this script; then the one setup_linux.sh creates.
VENV_CANDIDATES = ("venv", ".venv", "~/.venv/ts2")


def _venv_python(root):
    """Path to the interpreter inside virtualenv `root`, or None."""
    if not root:
        return None
    root = os.path.expanduser(root)
    if not os.path.isabs(root):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), root)
    names = (("Scripts", "python.exe"),) if sys.platform == "win32" else \
            (("bin", "python3"), ("bin", "python"))
    for parts in names:
        p = os.path.join(root, *parts)
        if os.path.isfile(p):
            return p
    return None


def _reexec_in_venv():
    """Re-run this script under a virtualenv that has the dependencies.

    Called only after an ImportError, so the common case costs nothing.  Two
    guards stop it looping forever if the venv is *also* incomplete: we never
    re-exec into the interpreter already running, and the child is marked with
    TS2_NO_REEXEC so it reports the real error instead of bouncing again.
    """
    if os.environ.get("TS2_NO_REEXEC"):
        return
    for root in (os.environ.get("TS2_VENV"),) + VENV_CANDIDATES:
        py = _venv_python(root)
        if not py:
            continue
        # Is this the interpreter already running?  Compare paths as written,
        # NOT via realpath(): a venv's bin/python3 is a symlink to the base
        # interpreter, so realpath() collapses the two and would make every
        # venv look identical to the system python it was built from.
        if os.path.abspath(py) == os.path.abspath(sys.executable):
            continue
        # Are we already inside this venv, started under some other name?
        if os.path.realpath(os.path.dirname(os.path.dirname(py))) == \
                os.path.realpath(sys.prefix):
            continue
        env = dict(os.environ, TS2_NO_REEXEC="1")
        argv = [py, os.path.abspath(__file__)] + sys.argv[1:]
        if os.name == "posix":
            try:
                os.execve(py, argv, env)      # replaces this process
            except OSError:
                continue
        else:
            # Windows has no real exec: os.exec* spawns a child and exits,
            # detaching from the parent's console and pipes.  Run the child
            # normally and forward its exit code.
            import subprocess
            try:
                sys.exit(subprocess.run(argv, env=env).returncode)
            except OSError:
                continue


def _has_fonts(d):
    try:
        return os.path.isdir(d) and any(
            f.lower().endswith((".ttf", ".otf", ".ttc")) for f in os.listdir(d))
    except OSError:
        return False


def _fix_qt_fonts():
    """Point OpenCV's bundled Qt at the system fonts.

    MUST be called *after* `import cv2`.  The opencv-python wheels ship a Qt
    built without fontconfig, and cv2/config-3.py unconditionally sets

        QT_QPA_FONTDIR = <site-packages>/cv2/qt/fonts

    on import -- a directory the wheel does not actually contain (only
    cv2/qt/plugins is there).  So every window prints

        QFontDatabase: Cannot find font directory .../cv2/qt/fonts

    Setting the variable before the import is useless: cv2 overwrites it.
    Qt reads it later, when the platform plugin builds its font database, so
    overriding it after the import and before the first window works.

    Cosmetic only -- OpenCV's putText uses its own built-in Hershey fonts, so
    the overlay was never affected.
    """
    if sys.platform == "win32":
        return
    if _has_fonts(os.environ.get("QT_QPA_FONTDIR", "")):
        return                      # already usable; leave a deliberate choice alone
    for d in ("/usr/share/fonts/truetype/dejavu",
              "/usr/share/fonts/truetype",
              "/usr/share/fonts",
              "/System/Library/Fonts"):
        if _has_fonts(d):
            os.environ["QT_QPA_FONTDIR"] = d
            return


try:
    import cv2
    import numpy as np
except ImportError:
    _reexec_in_venv()          # does not return if a usable venv was found
try:
    import cv2
    import numpy as np
except ImportError as exc:
    hint = (
        "On Windows a bare `pip` often belongs to a *different* Python than a\n"
        "bare `python`.  Install into this one explicitly:\n"
        f'  "{sys.executable}" -m pip install numpy opencv-python pyusb libusb\n'
        if sys.platform == "win32" else
        "Install into this interpreter:\n"
        f"  {sys.executable} -m pip install numpy opencv-python pyusb\n\n"
        "Ubuntu 24.04 refuses system-wide pip installs (PEP 668), so use a\n"
        "virtualenv.  This script picks one up automatically if it is at\n"
        "$TS2_VENV, ./venv, ./.venv or ~/.venv/ts2 -- then plain\n"
        "`./ts2_view.py` just works:\n"
        "  python3 -m venv ~/.venv/ts2\n"
        "  ~/.venv/ts2/bin/pip install numpy opencv-python pyusb\n\n"
        "If cv2 imports but dies on libGL.so.1:\n"
        "  sudo apt install libgl1 libglib2.0-0t64\n"
    )
    sys.exit(
        f"{exc}\n\n"
        f"Missing package for the interpreter you launched:\n"
        f"  {sys.executable}  (Python {sys.version.split()[0]})\n\n" + hint
    )

_fix_qt_fonts()                # after `import cv2` -- see the docstring

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ts2 import (HEIGHT, WIDTH, TS2, celsius, parse_header,  # noqa: E402
                 preview)

PALETTES = [("inferno", cv2.COLORMAP_INFERNO), ("grey", None),
            ("jet", cv2.COLORMAP_JET), ("hot", cv2.COLORMAP_HOT)]


def colorize(img8, cmap):
    return img8 if cmap is None else cv2.applyColorMap(img8, cmap)


def stretch(a, lo_pct=0.5, hi_pct=99.5):
    lo, hi = np.percentile(a, [lo_pct, hi_pct])
    if hi <= lo:
        hi = lo + 1
    return np.clip((a - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)


WINDOW = "Vantrue TS2"

# --- is the pointer actually over the image? ------------------------------
# highgui never reports a pointer-leave: it delivers EVENT_MOUSEMOVE only while
# the pointer is over the image, and its event enum has no "leave" at all.  So
# the last hover position would otherwise stay on screen forever.  Instead ask
# the window system where the pointer is and compare with the image rectangle,
# which cv2.getWindowImageRect() gives us in screen coordinates.
_pointer_backend = None


def _pointer_screen_pos():
    """Pointer position in screen coordinates, or None if unobtainable."""
    global _pointer_backend
    import ctypes
    if _pointer_backend is None:
        _pointer_backend = False
        try:
            if sys.platform == "win32":
                _pointer_backend = ("win", ctypes.windll.user32)
            else:
                # OpenCV's bundled Qt ships only the xcb platform plugin, so a
                # working highgui window implies X11 (or XWayland).
                x11 = ctypes.CDLL("libX11.so.6")
                x11.XOpenDisplay.restype = ctypes.c_void_p
                x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
                dpy = x11.XOpenDisplay(None)
                if dpy:
                    x11.XDefaultRootWindow.restype = ctypes.c_ulong
                    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
                    x11.XQueryPointer.argtypes = [
                        ctypes.c_void_p, ctypes.c_ulong,
                        ctypes.POINTER(ctypes.c_ulong),
                        ctypes.POINTER(ctypes.c_ulong),
                        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                        ctypes.POINTER(ctypes.c_uint)]
                    _pointer_backend = ("x11", x11, dpy,
                                        x11.XDefaultRootWindow(dpy))
        except (OSError, AttributeError):
            _pointer_backend = False
    if not _pointer_backend:
        return None
    try:
        if _pointer_backend[0] == "win":
            user32 = _pointer_backend[1]

            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

            pt = POINT()
            if not user32.GetCursorPos(ctypes.byref(pt)):
                return None
            return pt.x, pt.y
        _, x11, dpy, root = _pointer_backend
        rt = ctypes.c_ulong()
        ch = ctypes.c_ulong()
        rx, ry = ctypes.c_int(), ctypes.c_int()
        wx, wy = ctypes.c_int(), ctypes.c_int()
        mask = ctypes.c_uint()
        if not x11.XQueryPointer(dpy, root, ctypes.byref(rt), ctypes.byref(ch),
                                 ctypes.byref(rx), ctypes.byref(ry),
                                 ctypes.byref(wx), ctypes.byref(wy),
                                 ctypes.byref(mask)):
            return None
        return rx.value, ry.value
    except Exception:
        return None


def _pointer_over_image(window):
    """True / False, or None when it cannot be determined (then assume yes)."""
    pos = _pointer_screen_pos()
    if pos is None:
        return None
    try:
        x, y, w, h = cv2.getWindowImageRect(window)
    except cv2.error:
        return None
    if w <= 0 or h <= 0:
        return None
    return x <= pos[0] < x + w and y <= pos[1] < y + h


def _on_mouse(event, x, y, flags, cursor):
    """Track the pointer in window coordinates.

    highgui has no pointer-leave event, so this position simply goes stale
    once the pointer leaves; _pointer_over_image() decides whether to draw it.
    """
    if event == cv2.EVENT_MOUSEMOVE:
        cursor["x"], cursor["y"] = x, y
    elif event == cv2.EVENT_LBUTTONDOWN:
        cursor["pinned"] = None if cursor["pinned"] else (x, y)


def _spot(view, degc, px, py, scale, label_above, color):
    """Draw a crosshair at sensor pixel (px, py) labelled with its temperature."""
    wx, wy = int((px + 0.5) * scale), int((py + 0.5) * scale)
    cv2.drawMarker(view, (wx, wy), color, cv2.MARKER_CROSS, 13, 1, cv2.LINE_AA)
    text = f"{degc[py, px]:.1f}C"
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
    # keep the label inside the window whichever edge the pointer is near
    tx = min(max(wx + 10, 2), view.shape[1] - tw - 2)
    ty = wy - 10 if label_above else wy + th + 12
    ty = min(max(ty, th + 2), view.shape[0] - 4)
    cv2.rectangle(view, (tx - 3, ty - th - 3), (tx + tw + 3, ty + 4),
                  (0, 0, 0), cv2.FILLED)
    cv2.putText(view, text, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                color, 1, cv2.LINE_AA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grab", type=int, metavar="N",
                    help="save N frames and exit instead of showing a window")
    ap.add_argument("--outdir", default="../captures")
    ap.add_argument("--scale", type=int, default=3)
    args = ap.parse_args()

    cam = TS2()
    print("device:", ", ".join(f"{k}={v}" for k, v in cam.info_all().items()))
    print("status: 0x%02x" % cam.status())
    cam.start_stream()
    print("streaming (shutter/NUC runs first, ~3.5 s) ...")

    os.makedirs(args.outdir, exist_ok=True)
    saved = n = 0
    t0 = time.time()
    palette = 0
    cursor = {"x": None, "y": None, "pinned": None}
    if not args.grab:
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW, _on_mouse, cursor)
    try:
        for frame in cam.frames():
            n += 1
            hdr = parse_header(frame)
            grey = preview(frame)
            degc = celsius(frame)

            if args.grab:
                stamp = f"{args.outdir}/ts2_{saved:04d}"
                cv2.imwrite(stamp + "_preview.png", grey)
                np.save(stamp + "_celsius.npy", degc)
                saved += 1
                print(f"  saved {stamp}  {degc.min():.1f}..{degc.max():.1f} C")
                if saved >= args.grab:
                    break
                continue

            name, cmap = PALETTES[palette]
            view = colorize(stretch(degc), cmap)
            view = cv2.resize(view, (WIDTH * args.scale, HEIGHT * args.scale),
                              interpolation=cv2.INTER_CUBIC)
            hot = np.unravel_index(int(degc.argmax()), degc.shape)
            cv2.drawMarker(view, (hot[1] * args.scale, hot[0] * args.scale),
                           (255, 255, 255), cv2.MARKER_CROSS, 14, 1)

            # spot temperatures: pinned point (left-click) and live pointer
            def sensor_px(wx, wy):
                return (min(max(wx // args.scale, 0), WIDTH - 1),
                        min(max(wy // args.scale, 0), HEIGHT - 1))

            if cursor["pinned"]:
                px, py = sensor_px(*cursor["pinned"])
                _spot(view, degc, px, py, args.scale, False, (0, 255, 255))
            reading = ""
            if cursor["x"] is not None and _pointer_over_image(WINDOW) is not False:
                px, py = sensor_px(cursor["x"], cursor["y"])
                _spot(view, degc, px, py, args.scale, True, (255, 255, 255))
                reading = f"   [{px:3d},{py:3d}] {degc[py, px]:5.1f} C"

            fps = n / max(time.time() - t0, 1e-6)
            cv2.putText(view, f"{degc.min():5.1f} - {degc.max():5.1f} C   "
                              f"{fps:4.1f} fps   {name}{reading}",
                        (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (255, 255, 255), 1, cv2.LINE_AA)
            cv2.imshow(WINDOW, view)
            k = cv2.waitKey(1) & 0xFF
            if k == ord("q") or k == 27:
                break
            # Closed with the title-bar X?  highgui then reports the window as
            # no longer visible; once it is fully destroyed some builds raise
            # instead of returning, so treat both as "user closed it".
            try:
                if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    break
            except cv2.error:
                break
            if k == ord("p"):
                palette = (palette + 1) % len(PALETTES)
            if k == ord("f"):
                cam.shutter()
                print("  shutter fired")
            if k == ord("s"):
                stamp = f"{args.outdir}/ts2_{int(time.time())}"
                cv2.imwrite(stamp + ".png", view)
                np.save(stamp + "_celsius.npy", degc)
                print("  saved", stamp)
    finally:
        cam.stop_stream()
        cv2.destroyAllWindows()
        print(f"stopped after {n} frames ({n / max(time.time() - t0, 1e-6):.1f} fps)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (RuntimeError, PermissionError) as exc:
        sys.exit(f"\n{exc}\n")

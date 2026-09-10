#!/usr/bin/env python3
"""Live viewer / grabber for the Vantrue Thermal TS2.

    ./ts2_view.py                   live window
    ./ts2_view.py --grab 10         save 10 frames as PNG + .npy and exit
    ./ts2_view.py --no-smooth       unsmoothed palette range (see PaletteRange)

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


def script_dir():
    """Directory the application lives in.

    For a frozen build that is the executable's directory, not __file__ --
    PyInstaller puts __file__ inside the bundle, so anything shipped next to
    the program has to be looked up beside the executable instead.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(name):
    """Locate a data file that ships with the application, or None.

    Three places, because a frozen build can put it in any of them:
    PyInstaller's --onefile unpacks bundled data into a temporary directory it
    names in sys._MEIPASS; --onedir leaves it beside the executable; and a
    plain script has it beside the source.
    """
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(meipass)
    roots.append(script_dir())
    roots.append(os.path.dirname(os.path.abspath(__file__)))
    for root in roots:
        p = os.path.join(root, name)
        if os.path.exists(p):
            return p
    return None


def _venv_python(root):
    """Path to the interpreter inside virtualenv `root`, or None."""
    if not root:
        return None
    root = os.path.expanduser(root)
    if not os.path.isabs(root):
        root = os.path.join(script_dir(), root)
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


# --- palette range --------------------------------------------------------
# Taking each frame's percentiles as the display range makes the whole picture
# flash: a hot object entering or leaving shifts the mapping, and *every*
# pixel changes brightness at once even though the scene barely moved.
#
# So the bounds are damped instead, asymmetrically.  Opening out is quick, so
# something genuinely hot is never left clipped for long; closing back in is
# slow, so the picture settles instead of pumping.  Movement smaller than the
# dead band is ignored outright, which is what stops sensor noise alone from
# walking the bounds around frame to frame.
RANGE_LO_PCT, RANGE_HI_PCT = 0.5, 99.5
RANGE_EXPAND = 0.35        # fraction of the gap closed per frame, opening out
RANGE_CONTRACT = 0.03      # ... and closing in: ~2 s to settle at 25 fps
RANGE_DEADBAND = 0.25      # degC; smaller target moves are ignored entirely
RANGE_MIN_SPAN = 3.0       # degC; keeps a flat scene from amplifying noise


class PaletteRange:
    """The low/high temperatures the colour ramp is stretched between."""

    def __init__(self, smooth=True):
        self.smooth = smooth
        self.lo = None
        self.hi = None
        self._recent = []

    @staticmethod
    def _ease(current, target, expanding):
        if abs(target - current) < RANGE_DEADBAND:
            return current
        rate = RANGE_EXPAND if expanding else RANGE_CONTRACT
        return current + (target - current) * rate

    def update(self, degc):
        lo, hi = (float(v) for v in
                  np.percentile(degc, [RANGE_LO_PCT, RANGE_HI_PCT]))
        if self.smooth:
            # Belt and braces against a single freak frame.  ts2.frames() drops
            # misaligned reads, so a corrupt frame should never reach here --
            # but if one ever did, its nonsense temperatures would open the
            # range out fast and then take seconds to close again, which is
            # exactly the flash this class exists to prevent.  A median over
            # the last three targets discards any one-frame outlier outright,
            # and costs one frame of lag.
            self._recent.append((lo, hi))
            del self._recent[:-3]
            lo = sorted(v[0] for v in self._recent)[len(self._recent) // 2]
            hi = sorted(v[1] for v in self._recent)[len(self._recent) // 2]
        if self.lo is None or not self.smooth:
            self.lo, self.hi = lo, hi
        else:
            self.lo = self._ease(self.lo, lo, expanding=lo < self.lo)
            self.hi = self._ease(self.hi, hi, expanding=hi > self.hi)
        if self.hi - self.lo < RANGE_MIN_SPAN:
            mid = (self.hi + self.lo) / 2.0
            self.lo = mid - RANGE_MIN_SPAN / 2.0
            self.hi = mid + RANGE_MIN_SPAN / 2.0
        return self.lo, self.hi


def stretch(a, lo, hi):
    return np.clip((a - lo) * (255.0 / max(hi - lo, 1e-6)),
                   0, 255).astype(np.uint8)


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


# --- application icon -----------------------------------------------------
# Windows needs a real .ico file on disk: the title bar and taskbar button
# fall back to whatever python.exe is wearing otherwise, and there is no way
# to hand Win32 a PNG without building an HICON by hand.  X11 takes the PNGs
# embedded below, so on Linux the script carries its own icon and cannot lose
# it when copied somewhere else.
ICON_FILE = "icon.ico"

# Regenerate with tools/make_icon.py.  Quantised to 128 colours, which is
# visually identical here and halves the base64.
ICON_PNGS = (
    # 16 x 16
    (
        "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAMAAAAoLQ9TAAABgFBMVEUAAAB/f3/9//9G"
        "SlI3O0MvMzrO1NwOFRzDydFXW2OWnKcRDSizucMmKixPEVVuF1uOk513fIRdYWpOU1pl"
        "a3QQFiGTmaI8QUdcYGpLS1lobHbV2+OmrLZhZnB4f4r+py/ZVEh2fIc8PEuGjJiFipKZ"
        "mZmVI1WDG1j/vD5+g4x8gox/hZCSmZ+Qlp97f4t2fIiUnKNrcHiXn6efpbKepq+jKU+q"
        "qqpeY2ygpq9bbW2qsL1YXWa3vcq+xc6/1NTGxtTS0uHK0eTb4enqaT7wczr7lSj5/7wA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAACpKyXPAAAAgHRSTlMAAgX6/v7///38J/36/f7+7v6r9Kr/"
        "+f72Epj98fMY//+EESv/Bf7+//9OLijQQCtD/4AopP8Dw6QOJ9on/wwSESf//////wAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAEMj3awAAAC+SURBVHjaTc6HbsIwEADQy9muY+zEkE2BUsKmBboXtLRl/P8nYTsS"
        "ypNO8p1uGKBy8wkXiGN8Ut0hviHa1BVffPXgHp6JX0T8UN1F2S7bdvi9J+KiN8qaRra8"
        "g4EIeMqDrHNl+QlcBzKchpwp31IJEK73/duQj/5NQ+c7gVy+zk5HzcW8ZTxGQFJ9MB0p"
        "YU5kd+hJKBmhOaU0j9wVyVlMLUYjWG8KEQtBKvEK4Od++/c1bDjPu+rzNSZFrwbhDGZl"
        "DzV26JQcAAAAAElFTkSuQmCC"
    ),
    # 32 x 32
    (
        "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAMAAABEpIrGAAABgFBMVEUAAAA3O0P8/v/F"
        "y9NkaXIjJSswNDvN09sTFxmzucIeICSssbpWW2QtCk1bYWoTDClESFCcoquoqK2OlJ2R"
        "l6B/f39OUlpLDma1M1vBzdl3GnF1e4S7x9PPREVUEGqVJGuUm6alK2R8g43lWiyDipSl"
        "q7TV2+P3hwzP1OKBHXIODhuvtMPzdxJ8hI99go/Z5/Vtc31qcHpvd4N8g4341Ella3X9"
        "/+eDiJMAAP+HjZj6sRjFPU3Hx9X7qRD442rm5vf697btaR3rZCUQECCBh5L6wirH09+0"
        "w9Kyusa/v7+krriWnaiXnquNlZ6ZmZn7viZ+hI9wdoBye4NhbXlVZmYA//8AAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAChK1lNAAAAgHRSTlMA/gf/8/7+/v/9/v79//j//vwI9f4C"
        "/v//Kv/sKv///23/7P/s/v3/Ev//Ef9DKhLa/zKs/93/YwGF//8S//8P/////17/KRE/"
        "BEzfT4IF/4neHRUPAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAADwAAMcAAAGjSURBVHjavZNpV6MwFIZDvDcNaYBQKtRiV7vZamudRR23Gfd99///"
        "FQO0luLycZ5zWE548uZmgZAUr/Gdkq95GW4On7/8ukierttu2x3qt08pkHUzYusbIW/m"
        "TfPko0BjLmkibNG7pIFuT3tO2ZwkpEInAb3C3qhXGNzENdTpaJJAk9rrG3boea2W1wSA"
        "8XgM7zzuUF1OXRogFUNm+KAg327nZxQfdP4vBQYiCoHIpNnJpei4NiW9EPRXzo855yh8"
        "cyFFzpVL5LDFkHPLqgaWow3ICGfkwtf9rXKj1KgF2kA3l0n4oQOs1dLK2r9KLTgWArIC"
        "oHCqpaP9n/drlbLFUS2ki9SCjdwp73YPfv/9s1LTglH0vGKMfsqpUHkXhJLIZtjLsyFO"
        "u5XVWDBmREJS5G5UZCNwBEqVEa4Uin40zVKtqqfJwMgIoyYi71vBZKGkz+aEJbIX2vFS"
        "O/14qYHNJchlvVm6MdosfaE/N4AWgEbbrcCP9pMpkCwTsJMcGGh6zTD0WmDDHBu3dDE5"
        "coPBIaXnBZole2g/+RPSx/4j2+S/8AbWcSsi15oBCgAAAABJRU5ErkJggg=="
    ),
    # 48 x 48
    (
        "iVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAMAAABg3Am1AAABgFBMVEUAAADFy9P4/P43"
        "O0MwNDskJy0UFxlESFGzusN0eoTM0tl7gYwbHCFRVV6DiJJma3Tk6O6utb5VWWIVCDI9"
        "QUmWnKicoqu7wstuF3CTmqV/f39tc3yRJGcpCE7RRUVKDWrqZCdcYWq2NFYNDBypqbD6"
        "lwmHjpf9+9eJkJv4hg76txZREG6jqrajKmF/f79/f/9yeYakqrOrLF323lqepbDDO0/5"
        "wSFVqqr0eBfhVjQeISbU2eG0tMODHmyWpaWHh5aAiJKLkpyUmqWWlqWRmKN9hY6uWJOv"
        "hrwAAP+kq7eqs7yrsb5///+rtsC5x83ZTT7u8vff4+n26Xv37Ijl5f+nrr369rUdB0Gq"
        "sbyltLSqqv+wZp+hRoKaoq6YoKuZmcyQl6GMk56AiJKBhZKWfrN/h5F4h5bY2OV0f4p0"
        "XJhVZmZINW9BKGcmE0gAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAAA9Z8E3AAAAgHRSTlMA/gf+/v7+/v32/vD+/vr+//7+/v6c"
        "+///9AL//v7+///+//8J/7z/8P//+3L/BAIn////k/7/A//+//8R/xERftN8Ea55sYgB"
        "TzZPAjES//////8UI///YxEDpcioegXBtIE7i4ARFBidD73O6QAAAAAAAAAAAAAAAAAA"
        "AAAAAMFFjAUAAAK1SURBVHja7ZXZc9owEIeNJSGEsU2EkhhsA8ZQIEBbyH03TdK7Odr0"
        "vu/7/3/tyg4Bg8nw1pd+wwzDjD5+rNa7KEoMakGiTE3/qDq18fO6ZPrv/5QP2PuuTpVR"
        "Va6kQg6Vq9MI10CYAVIzH6cXUoFw418Kd9QBL9W+8EV9pw4zuMhqVO8XfTjytauFi8ZK"
        "f2tnt1rd3d86U9+fX+usejaSUAjPP1kyNMYa+bk8vBqMaYbBYtAOHioPIEe51+AIcZvZ"
        "Ouc6vHGMMTfHsW3jLrRVVfcoocw0CSVwkhLTZNROx5EwtW/wk+4z4jGPCEwCsICPM+lE"
        "DJuG1wahzTkjCG9kMkkgk9kAhc3HCWmDzoJwUzMpIvK47/uBQoSwLxPyghA4nvvzq1Vy"
        "3RwohND8ZGGJC5xJ+rkft28525ZVkgamNDVJqGqUkGTSheO9SqXiWC03mSQEGZOE56YM"
        "yH1+3Suul8vrxYoDGRDB5ycIH3QI8H+/6hTLzVqtWS72LNeHCNGdIGg6goCWs1yuLSwu"
        "rtTKy0GE0BvpxEjfNkNBXlHOcoprC4/q9ccLa8WOlZMXZaRG6VIdBMZlCVbnuLnytF6v"
        "rzSPO5YLRXBbpyMgzC9LYAhHQQjpFzWURmtAlHE9CkJYCgeyDb5r9UZuiXazp9khTrNz"
        "OEhom1QWUdquRPog+OZJNsJJOqyhqiHZ6Wcty6nITm+HnRbaWBe0MOH8WYIMy3Kc/rMk"
        "dFsWGQGHNShv8zR8Wl23NHhaNYHGCQU5DyKYh2R/HpCAwRbGXIQuC69VTpwGAzo0cYhA"
        "NxGdj5LongtypjUPhnRopj1ZgG1E8RA228rw1oBmwurwYGsEBeMxhHEU2Utc94K9hDCK"
        "RQ/2Utzmi8d4E2y+wW7d39158XXnSJ2I/CuI3d6TWC3E/j9clvCfKfkLSw9lZ+9UCyQA"
        "AAAASUVORK5CYII="
    ),
    # 64 x 64
    (
        "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAMAAACdt4HsAAABgFBMVEUAAADGzNM4PEQw"
        "NDwWGBwlKC3///9ESFGQl6Gvtb60usPj5+2Fi5ZUWWJPVFx0eYOco62Mkp2ztMMbHSF/"
        "f3+kqrNwGG7L0diRJWcPByq6wMlscnw9QUmzw9CosLx8go0pCFJkaXIMDBq2NFdMDWxd"
        "YmzPREUeICXlXDD9+tL3hQ45CmJXEW/sZybDO0/zdxj8pgz7mAijKmGAhpB8gowdB0P4"
        "xSjZTD2Ql5d7g4qHh5bdUTn7tRWoqLGbo66Ei5WrLl2mrbnD0uF/v7+xuMLDw9OhqLOO"
        "l6Snrbr15XCTpK+dpLD12UqkqrZVVVWpsL2Umqh/f5F7h4v04Vx8gYyCiJKumcj27IXy"
        "6Pb38pujqbWysrL/++D6vyHexOLDsdfNnL2gp7OgZqGsYpWgRYGjT4mQl6IAAP+NlJ6J"
        "kJuKkZmCiZaXPX1+hY7D0tJiP4l5KHpNJXU7EmQ0FVwAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAAAAAAAAADuX/4oAAAAgHRSTlMA//7+/v4B/vH+/f/8/v7++vUQ/gL+"
        "/v/+///+/hBB/v7+//7//v7+/////fv//v////+eYv///wsoEf7/D8ys/00RBCYQsxUv"
        "/w+7/2QDbSYOF/9ea3z/Yf+ECv//bXOBw5+px7rZAdm2IyfPnRG24dXt6gAAAAAAAAAA"
        "AAAAALDCmeYAAAQOSURBVHja7Zf5U9s4FMeNhCLjlXzEiUliU9uEHOQC0g2k4WZLodC7"
        "2273vu979/+f2SfZhCQ4cSe7+xvfmcw4x/ejJz3pPUVR/kdlcpHm9T+5epiT8L3y/Od7"
        "Qi/m8+eUd61Yd/u5efz31KVY5qs5ZgEBmFrk15YO5wK8PwTk7/7LCG4BbwnITKifGQXA"
        "2xsas+8njDCSxl8TY8yNjK4o2ZXsysnpxdmgf/FicJY9yWZfCYCmaWIjwZcTOj0Vxtiv"
        "KMcPVFW1GFNN04QXPFrqiKwEqZdP43OayTx/YFWoh2hoMd93XcP3LZ97yPPgs6nSK+zg"
        "TBJyTy59jDFyfUOnFRtVbFzxQoNxjD0/P11mxT3Yz2WU95THDCPMmUErhOgEU4J0wOjM"
        "8NyF5RkqUf8jsGcyBxwjgwk3Jlei1IbPzOWFWQqp+rFIoeVhwyA6BV/NXhSya/DMkWdp"
        "MwEuts4BcM7AD25MInekGrwnnJVmA9gxAO4z18ccxfbVYnF1VT6BnyI1BXAHAM9UJqK3"
        "F21wB383m0HQK0YEUuPmWwBMzqkcvxj8/vqzPxzHWWv2VoEHy8pdLRVwzGyCa/D7Yuvz"
        "L74pCAEiEEEQRJCZCjh0ESFi/D/ffFs4Knc6nfJRwWkFsBI25NPQUgD3GeUYJlD8680v"
        "3U51t16v71TLBYgBCMTjPJ8CeGosygCC1193OzvtjUajsVGXhKIMgagpgMOQYgAUm18V"
        "yjvtxube3uaWJMhJEDRjDhHgO53KGaw53Wq9sbn98OGj7a2N3U7BaUIIhOqhlryhlxdC"
        "CVCxLXLYaxXK1fbW9gfr6+sv9xrtalfOAWbHtVKiNOZJgM91AQgAsLux+Qj8n3y4vdWu"
        "HklADeuhofMk6QhJAKM0BeDCaU8QigGpU6gY4reJkoAfuD65iJ/CIpbjRfSoGyYChhEc"
        "hl6URichjTZUp+Q0LkMZQ2MbqbfmFBI2Uo2o7ySLYjS+lQOn0C1PbmXE9aUpAD0GKD+G"
        "ODpMTceJD1M39sMxpe5Csj9/tQbKHVbD0XFutpzC+HHGBFl6soZZUH6CguIRSQjWWo5z"
        "VVBEDnXdD0niLsDXAChpXg3HJa0XBFDSisOSFhpT98AQIItqiCAGWVSFZGkX43sqRYiK"
        "jjmpvDEEiLLu3yjrJCrrHEPTKIkmfUNUAI6vGwvVZWMREFvUEcI9z+Jit4SlBHtpCQCI"
        "nSvD1hZCyGjY2jxqY1f6QaGRIJEG2drGmyu0ZOivnGDZXONDMC0LWDbX6/bORHsHa41U"
        "aGj4HE89hTHWOLiA9j52wbBGLxh0tjj78iy+oty44pgqPKbp8vHwr8jwkvXbRX8w2B8M"
        "+tmT0+xsraxcX7KmXPNSr6K5mRfNdCm3utV/qH8AB9Ounzd6F7kAAAAASUVORK5CYII="
    ),
)


def claim_taskbar_identity():
    """Stop Windows filing our window under python.exe.

    Without an explicit AppUserModelID the taskbar button belongs to the
    interpreter, so it shows the Python icon whatever the window itself is
    wearing.  Must run before the first window exists.  A no-op elsewhere.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "vantrue.ts2.viewer")
    except Exception:                    # noqa: BLE001 - purely cosmetic
        pass


def _icon_images():
    """The embedded icons as (width, height, BGRA array), largest last."""
    import base64
    out = []
    for blob in ICON_PNGS:
        raw = base64.b64decode("".join(blob))
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED)
        if img is not None and img.ndim == 3 and img.shape[2] == 4:
            out.append((img.shape[1], img.shape[0], img))
    return out


def _win_set_icon(title):
    import ctypes
    from ctypes import wintypes
    path = resource_path(ICON_FILE)
    if not path:
        return False
    u = ctypes.windll.user32
    u.FindWindowW.restype = wintypes.HWND
    u.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    hwnd = u.FindWindowW(None, title)
    if not hwnd:
        return False
    u.LoadImageW.restype = wintypes.HANDLE
    u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                             wintypes.UINT, ctypes.c_int, ctypes.c_int,
                             wintypes.UINT]
    u.SendMessageW.restype = ctypes.c_ssize_t
    u.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                               ctypes.c_size_t, ctypes.c_ssize_t]
    IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010
    WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
    done = False
    for which, px in ((ICON_SMALL, 16), (ICON_BIG, 32)):
        h = u.LoadImageW(None, path, IMAGE_ICON, px, px, LR_LOADFROMFILE)
        if h:
            u.SendMessageW(hwnd, WM_SETICON, which, ctypes.c_ssize_t(h).value)
            done = True
    return done


def _x11_find_window(x11, dpy, win, title):
    """Depth-first search of the window tree for the one named `title`."""
    import ctypes
    name = ctypes.c_char_p()
    if x11.XFetchName(dpy, win, ctypes.byref(name)) and name.value:
        hit = name.value.decode("utf-8", "replace") == title
        x11.XFree(name)
        if hit:
            return win
    root = ctypes.c_ulong()
    parent = ctypes.c_ulong()
    kids = ctypes.POINTER(ctypes.c_ulong)()
    n = ctypes.c_uint()
    if not x11.XQueryTree(dpy, win, ctypes.byref(root), ctypes.byref(parent),
                          ctypes.byref(kids), ctypes.byref(n)):
        return 0
    found = 0
    for i in range(n.value):
        found = _x11_find_window(x11, dpy, kids[i], title)
        if found:
            break
    if kids:
        x11.XFree(kids)
    return found


def _x11_set_icon(title):
    """Publish the embedded icons as _NET_WM_ICON on the window."""
    import ctypes
    _pointer_screen_pos()                # forces the ctypes backend to load
    be = _pointer_backend
    if not be or be[0] != "x11":
        return False
    _, x11, dpy, root = be
    x11.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                               ctypes.POINTER(ctypes.c_char_p)]
    x11.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                               ctypes.POINTER(ctypes.c_ulong),
                               ctypes.POINTER(ctypes.c_ulong),
                               ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                               ctypes.POINTER(ctypes.c_uint)]
    x11.XInternAtom.restype = ctypes.c_ulong
    x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    x11.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong,
                                    ctypes.c_ulong, ctypes.c_ulong,
                                    ctypes.c_int, ctypes.c_int,
                                    ctypes.c_void_p, ctypes.c_int]
    win = _x11_find_window(x11, dpy, root, title)
    if not win:
        return False

    # _NET_WM_ICON is width, height, then width*height pixels of 0xAARRGGBB,
    # repeated for every size offered.  Format 32 means "long" to Xlib, which
    # is 64 bits on LP64 -- hence c_ulong rather than c_uint32.
    data = []
    for w, h, bgra in _icon_images():
        px = (bgra[:, :, 3].astype(np.uint32) << 24 |
              bgra[:, :, 2].astype(np.uint32) << 16 |
              bgra[:, :, 1].astype(np.uint32) << 8 |
              bgra[:, :, 0].astype(np.uint32))
        data.extend((w, h))
        data.extend(px.ravel().tolist())
    if not data:
        return False
    buf = (ctypes.c_ulong * len(data))(*data)
    atom = x11.XInternAtom(dpy, b"_NET_WM_ICON", False)
    XA_CARDINAL, PROP_MODE_REPLACE = 6, 0
    x11.XChangeProperty(dpy, win, atom, XA_CARDINAL, 32, PROP_MODE_REPLACE,
                        ctypes.cast(buf, ctypes.c_void_p), len(data))
    x11.XFlush(dpy)
    return True


def set_window_icon(title):
    """Dress the highgui window in our own icon.

    OpenCV exposes no icon API at all, so this goes around it and talks to the
    window system: WM_SETICON on Windows, _NET_WM_ICON on X11.  The window has
    to exist already, so call it after the first imshow.  Cosmetic -- every
    failure here is silent.
    """
    try:
        if sys.platform == "win32":
            return _win_set_icon(title)
        return _x11_set_icon(title)
    except Exception:                    # noqa: BLE001 - purely cosmetic
        return False


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
    ap.add_argument("--no-smooth", action="store_true",
                    help="recompute the palette range from every frame, as "
                         "before; useful for comparing against the smoothing")
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
    prange = PaletteRange(smooth=not args.no_smooth)
    cursor = {"x": None, "y": None, "pinned": None}
    # the window manager may not have mapped the window on the very first
    # imshow, so the icon gets a few frames to take
    icon_tries = 0
    if not args.grab:
        claim_taskbar_identity()         # must precede the first window
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(WINDOW, _on_mouse, cursor)
        icon_tries = 20
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
            lo, hi = prange.update(degc)
            view = colorize(stretch(degc, lo, hi), cmap)
            view = cv2.resize(view, (WIDTH * args.scale, HEIGHT * args.scale),
                              interpolation=cv2.INTER_CUBIC)
            hot = np.unravel_index(int(degc.argmax()), degc.shape)
            cv2.drawMarker(view, (hot[1] * args.scale, hot[0] * args.scale),
                           (0, 0, 255), cv2.MARKER_CROSS, 14, 1)

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
            cv2.putText(view, f"{lo:5.1f} - {hi:5.1f} C   "
                              f"max {degc.max():5.1f}   "
                              f"{fps:4.1f} fps   {name}{reading}",
                        (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (255, 255, 255), 1, cv2.LINE_AA)
            cv2.imshow(WINDOW, view)
            if icon_tries:
                icon_tries = 0 if set_window_icon(WINDOW) else icon_tries - 1
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

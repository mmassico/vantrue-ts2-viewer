# vantrue-ts2-viewer

Unofficial userspace driver and live viewer for the **Vantrue TS2** USB thermal
camera — 256×192 radiometric at 25 fps, on **Linux and Windows**.

The TS2 ships with a Windows-only application. This project reimplements its
USB protocol so the camera works anywhere, and exposes the **full radiometric
plane** — a real temperature for every pixel, not just a false-colour picture.

![Live viewer](./docs/gui.png?v=1.5)

---

## What you get

- **Live viewer** with four palettes, a hover spot-thermometer, and a
  click-to-pin second reading.
- **Real temperatures.** Every frame carries a 256×192 16-bit radiometric
  plane; `celsius(frame)` hands you a `float32` array in °C.
- **A small, dependency-light driver.** `ts2.py` is ~250 lines over `pyusb`.
  No kernel module, no out-of-tree driver, no Zadig.
- **A documented protocol.** [`docs/PROTOCOL.md`](docs/PROTOCOL.md) describes
  the command channel, framing and temperature scale in enough detail to port
  this to another language.
- **Its own window icon** on both platforms, regenerable with
  [`tools/make_icon.py`](tools/make_icon.py).

## Install

### Linux

```bash
git clone https://github.com/mmassico/vantrue-ts2-viewer.git
cd vantrue-ts2-viewer
./setup_linux.sh            # apt deps, udev rule, virtualenv
```

Then **unplug and replug the camera** — udev applies the rule when the device
enumerates, so one that is already plugged in keeps its old permissions.

```bash
./ts2_view.py
```

`./setup_linux.sh --check` diagnoses an existing install without changing
anything.

No kernel driver claims a vendor-class interface, so there is nothing to
unbind or blacklist. `uvcvideo` is not involved — this is not a UVC camera.

### Windows

Works against the WinUSB binding installed by the vendor package; no driver
replacement needed.

```
pip install pyusb libusb numpy opencv-python
python ts2_view.py
```

The vendor application must not be running — it holds the WinUSB handle
exclusively, and you will get `Access denied`.

## Usage

```
./ts2_view.py                 live window
./ts2_view.py --grab 10       save 10 frames (PNG preview + .npy of °C) and exit
./ts2_view.py --scale 4       bigger window
./ts2_view.py --no-smooth     unsmoothed palette range, for comparison
```

The header shows the **palette range** — the two temperatures the colour ramp
is stretched between — followed by the true scene maximum, which can sit above
the top of the range while it catches up.

Those bounds are damped rather than recomputed from scratch each frame. Taking
each frame's percentiles independently makes the whole picture flash whenever
something hot enters or leaves, because the mapping snaps and every pixel
changes brightness at once. Instead the range opens out quickly, so nothing
genuinely hot stays clipped, closes back in slowly so the image settles, and
ignores movement below a dead band so sensor noise alone cannot walk it around.
On a static scene the bounds hold perfectly still. `--no-smooth` restores the
old per-frame behaviour if you want to see the difference; the constants are
at the top of `PaletteRange` in `ts2_view.py`.

| Input | Action |
|---|---|
| hover | temperature under the pointer, with its sensor pixel |
| left-click | pin a second spot reading (click again to clear) |
| `p` | cycle palette |
| `f` | fire the shutter (flat-field correction) |
| `s` | save the current frame as PNG + `.npy` |
| `q` / Esc / window ✕ | quit |

Readings are single-pixel spot values taken straight from the radiometric
plane — no spatial averaging.

## Use it as a library

```python
from ts2 import TS2, celsius, preview

with TS2() as cam:
    print(cam.info_all())          # name, firmware, serial, module
    cam.start_stream()
    for frame in cam.frames():
        degc = celsius(frame)      # float32 (192, 256), degrees Celsius
        grey = preview(frame)      # uint8  (192, 256), AGC'd 8-bit image
        print(f"{degc.min():.1f} .. {degc.max():.1f} C   centre {degc[96,128]:.1f}")
        break
```

The module runs a shutter/NUC calibration when streaming starts (an audible
click), so **the first frame arrives about 3.5 s after `start_stream()`**.
`frames()` absorbs the timeouts during that window for you.

## How it works, briefly

The camera is a composite vendor-class device (`3474:45f2`) whose imaging
module is a 256×192 InfiRay-family sensor. Commands travel over EP0 vendor
requests carrying an 18-byte block checksummed with CRC-16/XMODEM. Video is a
single bulk endpoint: `SET_INTERFACE(1, 1)` plus one vendor request starts it,
and each frame is one 197644-byte transfer containing an 8-bit preview image,
a metadata block, and the 16-bit radiometric plane. Temperature is
`raw16 / 64 − 273.15` °C.

Full details, including everything measured rather than assumed, are in
[`docs/PROTOCOL.md`](docs/PROTOCOL.md).

## Accuracy

The radiometric plane is the **sensor** scale. The vendor software applies
emissivity, distance and ambient-temperature compensation on the host; this
project does not. Readings are self-consistent and sensible for everyday use,
but treat absolute accuracy against a calibrated reference with caution.

## Status and compatibility

Developed and tested against firmware `00.00.01.03`, module `P3`, on Ubuntu
24.04 (kernel 6.8) and Windows 11. Other firmware revisions are untested —
`./setup_linux.sh --check` and `--grab` are the quickest way to find out.

Not implemented: video recording, emissivity/distance correction, and most of
the command set (the device exposes far more than the handful of commands used
here — see the protocol notes).

Issues and pull requests welcome, especially reports from other TS2 units.

## Packaging

PyInstaller works, and the icon needs to travel two ways: baked into the
executable so Explorer shows it, and as a real file so the running window can
load it.

```bash
# Windows  (--add-data separator is ';')
pyinstaller --onefile --windowed --icon icon.ico --add-data "icon.ico;." ts2_view.py

# Linux    (--add-data separator is ':')
pyinstaller --onefile --icon icon.ico --add-data "icon.ico:." ts2_view.py
```

`resource_path()` looks in `sys._MEIPASS` (where `--onefile` unpacks bundled
data), then beside the executable (`--onedir`), then beside the source — so
the same code finds the icon frozen or not.

## Requirements

Python 3.8+, `pyusb`, `numpy`, `opencv-python` (plus `libusb` on Windows).
Regenerating the icon additionally needs `pillow`; running it does not.

## Licence

MIT — see [LICENSE](LICENSE).

## Disclaimer

This is an **unofficial** project. It is not affiliated with, endorsed by, or
supported by Vantrue or Shenzhen Yidian Zhuoyue Technology Co., Ltd. "Vantrue"
and "TS2" are used only to identify the hardware this software talks to.

It contains no vendor code. The protocol was derived by observing USB traffic
from a device the author owns and writing an independent implementation, for
the purpose of interoperability. No technical protection measure was
circumvented — the protocol is neither encrypted nor authenticated.

Use at your own risk.

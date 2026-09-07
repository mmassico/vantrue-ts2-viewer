# Vantrue Thermal TS2 — USB protocol

Reverse-engineered from `Ts2_v2.pcapng` (USBPcap trace of the Windows app),
then **verified live** against the camera with an independent Python
implementation (`ts2.py`). Everything below has been executed against the
hardware, not just inferred from the trace.

---

## 1. Device identity

| | |
|---|---|
| idVendor | `0x3474` — "Shenzhen Yidian Zhuoyue Technology Co., Ltd." (Vantrue) |
| idProduct | `0x45F2` — "TS2" |
| bcdDevice | `0x0200` |
| Class | `0xEF / 0x02 / 0x01` (IAD), USB 2.0 High-Speed, self-powered |
| Windows driver | WinUSB, via `drivers/inf/TS2.inf`, interface GUID `{80F70370-086B-C345-5A54-59DE4519AF2A}` |

Configuration 1 ("iAP Interface") — one IAD spanning two interfaces:

| Interface | Alt | Class/Sub/Proto | iInterface | Endpoints |
|---|---|---|---|---|
| 0 | 0 | `FF/F0/00` | "iAP Interface" | bulk IN `0x84`, bulk OUT `0x05` (never used by the app) |
| 1 | 0 | `FF/F0/01` | "com.ydzy.ts002" | none |
| 1 | 1 | `FF/F0/01` | "com.ydzy.ts002" | **bulk IN `0x81`**, bulk OUT `0x02` |

All bulk endpoints are 512-byte max packet.

The `iAP`/`com.ydzy.ts002` strings are Apple iAP2 accessory-protocol
leftovers — the same hardware is an MFi accessory for iOS. On Windows and
Linux they are irrelevant; only the vendor requests and EP `0x81` matter.

Because both interfaces sit in one IAD function, `usbccgp` exposes them as a
single `MI_00` child, and WinUSB owns **both**. That is why the app can
`SET_INTERFACE(1, alt 1)` and read EP `0x81` through a handle opened on
interface 0.

The imaging module reports itself as **"P3"** — a 256×192 InfiRay-family
sensor at 25 fps. (The bundled Windows app is a rebranded InfiRay *P2Viewer*;
its `libirparse` / `libirprocess` / `libirtemp` DLLs and the Android app's
`com.energy.iruvc` JNI classes are the InfiRay SDK.)

---

## 2. Command channel — EP0 vendor requests

Three vendor requests, recipient = **interface** (`wIndex = 0`):

| bmRequestType | bRequest | Dir | Length | Meaning |
|---|---|---|---|---|
| `0x41` | `0x20` | OUT | 18 | submit command block |
| `0xC1` | `0x22` | IN | 1 | poll state |
| `0xC1` | `0x21` | IN | N | read reply |

State byte from `0x22`: `0x02` = reply ready, `0x03` = idle/done.

Normal exchange:

```
0x41/0x20  -> 18-byte command
0xC1/0x22  <- 0x02          (poll until ready)
0xC1/0x21  <- N bytes       (N = reply length declared in the command)
0xC1/0x22  <- 0x03          (drain back to idle)
```

Commands with no reply (`reply_len == 0`) skip straight to `0x03`.

### Command block (18 bytes, little-endian)

```
 offset  size  field
   0      1    0x01                       constant
   1      1    group
   2      1    op        0x81 = get, 0x43 = action
   3      1    0x00
   4      8    parameters
  12      4    uint32  reply length
  16      2    uint16  CRC-16/XMODEM over bytes 0..15, stored little-endian
```

CRC is **CRC-16/XMODEM**: poly `0x1021`, init `0x0000`, no input/output
reflection, no final XOR. This was recovered by brute-forcing the parameter
space against all eight distinct commands in the trace — a unique match.

### Known commands

| group | op | params[0] | reply | Meaning | Observed value |
|---|---|---|---|---|---|
| `0x01` | `0x81` | `0x01` | 30 | device name | `TS2` |
| `0x01` | `0x81` | `0x02` | 12 | firmware version | `00.00.01.03` |
| `0x01` | `0x81` | `0x06` | 64 | part number | `TS2-1A00…` (redacted) |
| `0x01` | `0x81` | `0x07` | 64 | serial number | `TS2250…` (redacted) |
| `0x01` | `0x81` | `0x0A` | 64 | module version | `P3-00.01` |
| `0x01` | `0x81` | `0x0F` | 64 | module type | `P3` |
| `0x2F` | `0x81` | `0x00` | 1 | status (see below) | `0x35` fresh, else `0x01` |
| `0x36` | `0x43` | — | 0 | shutter / flat-field correction | audible click |

The `0x2F` byte is **not** a stream-state flag, despite appearing next to the
start sequence in the trace. Measured on the hardware: it reads `0x35` only on
a freshly enumerated device, and `0x01` from the first `start_stream()`
onwards -- while streaming *and* after stopping. Its exact meaning is
unresolved; do not gate logic on it.

Also measured: `SET_INTERFACE(1, 0)` tears down the endpoint but does not stop
the sensor -- re-selecting alt 1 immediately yields data again without
re-sending `0xEE`. There is no known command that powers the module down;
unplugging is the only certain way.

Replies are NUL-padded ASCII. The group/op bytes read naturally as a 16-bit
command word (`0x8101`, `0x812F`, `0x4336`) with `0x80` marking a getter,
matching InfiRay's IRCMD convention.

---

## 3. Streaming

Start:

```
SET_INTERFACE(interface=1, alt=1)        standard request, wValue=1 wIndex=1
vendor 0x40 / 0xEE, wValue=0, wIndex=1, wLength=0
```

Note `0xEE` uses recipient **device** (`0x40`), not interface.

Stop:

```
SET_INTERFACE(interface=1, alt=0)
```

That is the entire sequence — no bulk OUT traffic is involved at any point,
and no configuration of resolution/format is needed.

On start the module runs a shutter/NUC calibration (the mechanical click you
hear). **The first frame arrives ~3.5 s after the start request**; reads
before that time out. Tolerate timeouts during this window rather than
treating them as errors.

If the endpoint stalls (pipe error), `clear_halt(0x81)` recovers it. Calling
`libusb_reset_device` is *not* recommended — it leaves this device wedged
until it is physically replugged.

---

## 4. Frame format

Each frame is **one 197644-byte bulk transfer** on EP `0x81`, terminated by a
short packet, immediately followed by a standalone **12-byte trailer packet**.

If you request exactly 197632 bytes you will get a truncated frame plus two
stray 12-byte transfers — request 197644 (or more) instead.

```
 offset    size    content
      0      12    frame header
     12   98304    256x192 YUYV422 preview  (U and V pinned to 0x80)
  98316    1012    parameter / metadata block
  99328      12    radiometric-plane header
  99340   98304    256x192 uint16 little-endian radiometric plane
             ----
           197644
```

### Preview plane

YUYV422 with both chroma bytes fixed at `0x80`, i.e. a plain 8-bit greyscale
image in the even bytes. It has automatic gain control applied by the module,
so it is good for display but **not** linear in temperature.

```python
grey = np.frombuffer(frame, np.uint8, 98304, 12).reshape(192, 256, 2)[:, :, 0]
```

### Radiometric plane

Unsigned 16-bit, little-endian, one sample per pixel, in **1/64 kelvin**:

```
temperature_celsius = raw16 / 64.0 - 273.15
```

```python
raw  = np.frombuffer(frame, '<u2', 256*192, 99340).reshape(192, 256)
degc = raw.astype('float32') / 64.0 - 273.15
```

Verified: Spearman rank correlation between the preview plane and the
radiometric plane is **0.9964** (the residual is the preview's AGC
non-linearity), and an indoor scene reads 17.7–31.6 °C.

This is the *sensor* scale. Emissivity, distance and ambient-temperature
compensation are applied on the host by InfiRay's `libirtemp`; for absolute
accuracy against a reference source you would still want those corrections.

### 12-byte header

Used for the frame header, the radiometric-plane header, and the standalone
trailer packet:

```
 offset  size  field
   0      1    0x0C     header length
   1      1    0x8C..0x8F  bit0 = frame parity, bit1 = 0 frame / 1 trailer
   2      4    uint32   hardware timestamp A  (~15.01 MHz tick)
   6      4    uint32   hardware timestamp B
  10      2    uint16   milliseconds, +40 per frame (25 fps)
```

---

## 5. Files

| File | Purpose |
|---|---|
| `ts2.py` | the driver — protocol, framing, decoding |
| `ts2_view.py` | live viewer / grabber (`--grab N` to save frames) |
| `setup_linux.sh` | one-shot Linux setup; `--check` diagnoses an install |

Viewer controls: **hover** for the temperature under the pointer (shown at the
crosshair and in the header with its sensor pixel; it disappears when the
pointer leaves the image), **left-click** to pin a second spot reading that
stays put (click again to clear), `p` palette, `f` shutter/FFC, `s` save
PNG + `.npy`, `q`/Esc/window-X quit. Readings are single-pixel spot values
straight from the radiometric plane — no spatial averaging.

highgui has no pointer-leave event (its event enum has none), so the viewer
asks the window system for the pointer position — `GetCursorPos` on Windows,
`XQueryPointer` via ctypes on X11 — and compares it against
`cv2.getWindowImageRect()`, which is in screen coordinates. If neither is
available the reading simply stays visible.

| `99-vantrue-ts2.rules` | udev rule granting non-root access |

---

## 6. Running it

### Linux (the target)

```bash
git clone https://github.com/mmassico/vantrue-ts2-viewer.git
cd vantrue-ts2-viewer
./setup_linux.sh          # apt deps, udev rule, venv
# UNPLUG AND REPLUG the camera
./ts2_view.py
```

`ts2_view.py` finds its own virtualenv: if `cv2`/`numpy` are missing from the
interpreter that started it, it re-executes itself using the first python it
finds among `$TS2_VENV`, `./venv`, `./.venv` and `~/.venv/ts2`, forwarding the
command line. So `./ts2_view.py` works straight from a shebang with no
`activate` and no long venv path. It never re-executes into the interpreter
already running, and marks the child with `TS2_NO_REEXEC`, so an incomplete
venv reports the real error instead of looping.

`./setup_linux.sh --check` diagnoses an existing install without changing
anything.

Doing it by hand:

```bash
sudo apt install python3-venv libusb-1.0-0 libgl1 libglib2.0-0t64
sudo cp 99-vantrue-ts2.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# replug the camera
python3 -m venv ~/.venv/ts2
~/.venv/ts2/bin/pip install numpy opencv-python pyusb
~/.venv/ts2/bin/python ts2_view.py
```

No kernel driver claims a vendor-class (`FF/F0`) interface, so there is
nothing to unbind and no out-of-tree module to build — libusb takes the
device directly. `ts2.py` is platform-independent; only `open_backend()`
has a Windows-specific path to locate `libusb-1.0.dll`.

Three things that commonly bite on Ubuntu 24.04:

- **udev timing.** The rule is applied when the device *enumerates*. Installing
  it while the camera is already plugged in changes nothing until you replug.
  Symptom: `Access denied`. `ts2.py` detects this and prints the fix.
- **PEP 668.** `pip install` outside a virtualenv is refused with
  "externally-managed-environment". Use the venv above.
- **`libGL.so.1`.** `opencv-python` needs it and a minimal/server install
  lacks it: `sudo apt install libgl1 libglib2.0-0t64`. On a headless box use
  `--grab N` (no window) or install `opencv-python-headless` instead.
- **`QFontDatabase: Cannot find font directory .../cv2/qt/fonts`.** Cosmetic,
  and the fix has one non-obvious ordering requirement. The `opencv-python`
  wheel bundles a Qt built without fontconfig, and `cv2/config-3.py`
  *unconditionally* sets `QT_QPA_FONTDIR` to `<site-packages>/cv2/qt/fonts`
  when `cv2` is imported — a directory the wheel does not ship (only
  `cv2/qt/plugins` exists). Setting `QT_QPA_FONTDIR` **before** the import
  therefore achieves nothing; cv2 overwrites it. Qt only reads the variable
  later, when the platform plugin builds its font database, so
  `ts2_view.py` calls `_fix_qt_fonts()` *after* `import cv2` and before the
  first window. `sudo apt install fonts-dejavu-core` if the machine has no
  system fonts at all. OpenCV's own `putText` uses built-in Hershey fonts, so
  the overlay was never affected.

Non-issues, for reassurance: no Zadig equivalent, no `modprobe -r`, no
blacklisting, and `uvcvideo` is irrelevant — this device is not a UVC camera
on any OS.

### Windows

Works as-is against the stock WinUSB binding installed by the Vantrue
package — no driver replacement (no Zadig) needed:

```
pip install pyusb libusb numpy opencv-python
python ts2_view.py
```

Two things that bite on Windows:

- **`python` and `pip` may be different interpreters.** With several Pythons
  installed, `pip install ...` can report "Requirement already satisfied"
  while `python ts2_view.py` dies on `No module named 'cv2'`. Check with
  `pip -V` and `python -V`; install explicitly with
  `py -3.14 -m pip install ...` if they disagree. `ts2_view.py` detects this
  and prints the exact command to run.
- **The Vantrue Thermal application must not be running.** It holds the
  WinUSB handle exclusively and you will get `Access denied` on claim.

---

## 7. Re-capturing the traffic yourself

USBPcap's default 65535-byte snaplen truncates every video transfer to the
first ~33 % — enough to identify the preview plane, but not the radiometric
one. Raise it:

```
USBPcapCMD.exe -d \\.\USBPcap1 -o out.pcapng -s 262144
```

USBPcap records **every device on the selected controller**, not just the
camera, so a raw capture also contains HID and Bluetooth traffic from your
other peripherals. Filter it to the camera before sharing one.

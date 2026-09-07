"""
Vantrue Thermal TS2 -- userspace driver (Windows/WinUSB + Linux).

USB identity
------------
    idVendor  0x3474   "Shenzhen Yidian Zhuoyue Technology Co., Ltd."
    idProduct 0x45F2   "TS2"
    IAD, vendor class FF / subclass F0, two interfaces:
      if0 alt0  FF/F0/00 "iAP Interface"   bulk IN 0x84, bulk OUT 0x05  (unused)
      if1 alt0  FF/F0/01 "com.ydzy.ts002"  no endpoints
      if1 alt1  FF/F0/01                   bulk IN 0x81, bulk OUT 0x02

Sensor is a 256x192 InfiRay-family module (reports itself as "P3") at 25 fps.

Command channel  (EP0 vendor requests, recipient = interface 0)
--------------------------------------------------------------
    0x41 / 0x20  OUT  18 bytes   submit command block
    0xC1 / 0x22  IN    1 byte    poll state: 0x02 = reply ready, 0x03 = idle
    0xC1 / 0x21  IN    N bytes   read reply

Command block (18 bytes, little-endian):
    [0]      0x01              constant
    [1]      group
    [2]      op                0x81 = get, 0x43 = action
    [3]      0x00
    [4..11]  parameters
    [12..15] uint32 reply length
    [16..17] CRC-16/XMODEM (poly 0x1021, init 0x0000, no reflection) over [0..15]

Streaming
---------
    SET_INTERFACE(1, 1)                    -- activates bulk IN 0x81
    vendor 0x40 / 0xEE  wIndex = 1         -- start
    ... read 197644-byte transfers from EP 0x81 ...
    SET_INTERFACE(1, 0)                    -- stop

The module runs a shutter/NUC calibration on start (audible click); the first
frame arrives roughly 3.5 s after the start request.

Frame layout (197644 bytes, one bulk transfer terminated by a short packet)
--------------------------------------------------------------------------
       0 ..     11   frame header, 12 bytes
      12 ..  98315   256x192 YUYV422 preview, chroma pinned to 0x80 (AGC'd grey)
   98316 ..  99327   1012-byte parameter/metadata block
   99328 ..  99339   radiometric-plane header, 12 bytes
   99340 .. 197643   256x192 uint16 LE radiometric plane

Each frame transfer is followed by a standalone 12-byte trailer packet that
carries the same header layout with bit1 of byte[1] set.

    temperature_celsius = raw16 / 64.0 - 273.15

Header layout (12 bytes), also emitted as standalone 12-byte transfers:
    [0]      0x0C        header length
    [1]      0x8C..0x8F  bit0 = frame parity, bit1 = 0 frame / 1 trailer
    [2..5]   uint32      hardware timestamp A (~15 MHz tick)
    [6..9]   uint32      hardware timestamp B
    [10..11] uint16      milliseconds, +40 per frame (25 fps)
"""
import struct
import time

VID, PID = 0x3474, 0x45F2
CMD_IF, VIDEO_IF, VIDEO_ALT = 0, 1, 1
EP_VIDEO = 0x81

WIDTH, HEIGHT = 256, 192
FRAME_BYTES = 197644
HDR_OFF, HDR_LEN = 0, 12
IMG_OFF, IMG_LEN = 12, WIDTH * HEIGHT * 2          # 98304, YUYV422
META_OFF, META_LEN = 98316, 1012
TMP_HDR_OFF = 99328
TMP_OFF, TMP_LEN = 99340, WIDTH * HEIGHT * 2       # 98304, uint16 LE

STATE_REPLY_READY, STATE_IDLE = 0x02, 0x03

# Command group 0x2F returns one byte whose meaning is not fully pinned down.
# 0x35 has only ever been observed on a freshly enumerated device, before the
# first start_stream(); afterwards it reads 0x01 -- while streaming AND after
# stop_stream().  So it is NOT a stream-state flag; do not gate anything on it.
STATUS_FRESH = 0x35


def crc16_xmodem(buf):
    c = 0
    for b in buf:
        c ^= b << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def build_cmd(group, op, params=b"", reply_len=0):
    body = bytes((0x01, group, op, 0x00)) + params.ljust(8, b"\0")[:8]
    body += struct.pack("<I", reply_len)
    return body + struct.pack("<H", crc16_xmodem(body))


# command group 0x01 / op 0x81: params[0] selects the field
INFO_FIELDS = {
    "name":       (0x01, 30),
    "fw_version": (0x02, 12),
    "part_no":    (0x06, 64),
    "serial":     (0x07, 64),
    "module_ver": (0x0A, 64),
    "module":     (0x0F, 64),
}


PERMISSION_HELP = """\
The TS2 is present but this user may not open it.

On Linux, install the udev rule that ships next to this file:

    sudo cp 99-vantrue-ts2.rules /etc/udev/rules.d/
    sudo udevadm control --reload-rules && sudo udevadm trigger

then UNPLUG AND REPLUG the camera (the rule is applied at enumeration, so a
device that is already plugged in keeps its old permissions).

Running as root works too, but the udev rule is the right fix.

On Windows this error instead means another process owns the device -- close
the Vantrue Thermal application."""


def open_backend(dll_path=None):
    """Return a libusb backend.

    On Windows, prefer the DLL bundled with the `libusb` PyPI package so the
    user does not have to place libusb-1.0.dll by hand.  On Linux and macOS
    the system libusb found by pyusb is used.
    """
    import sys
    import usb.backend.libusb1
    if dll_path is None and sys.platform == "win32":
        import os
        try:
            import libusb
            arch = "x86_64" if sys.maxsize > 2 ** 32 else "x86"
            p = os.path.join(os.path.dirname(libusb.__file__), "_platform",
                             "windows", arch, "libusb-1.0.dll")
            dll_path = p if os.path.exists(p) else None
        except ImportError:
            pass
    if dll_path is None:
        backend = usb.backend.libusb1.get_backend()
        if backend is None:
            raise RuntimeError(
                "no libusb backend found -- install libusb "
                "(Debian/Ubuntu: sudo apt install libusb-1.0-0)")
        return backend
    return usb.backend.libusb1.get_backend(find_library=lambda _: dll_path)


class TS2:
    def __init__(self, backend=None):
        import usb.core
        self._usb = usb.core
        self.dev = usb.core.find(idVendor=VID, idProduct=PID,
                                 backend=backend or open_backend())
        if self.dev is None:
            raise RuntimeError(
                "TS2 not found (VID 0x3474 / PID 0x45F2). "
                "Is it plugged in? `lsusb | grep 3474` on Linux.")
        try:
            self.dev.set_configuration(1)
        except usb.core.USBError as exc:
            # EACCES means we may not touch the device at all -- worth an
            # explanation.  Anything else is benign: libusb's WinUSB backend
            # cannot set the configuration, and on Linux the device is already
            # configured (and may report EBUSY once an interface is claimed).
            if exc.errno == 13:
                raise PermissionError(PERMISSION_HELP) from exc
        except NotImplementedError as exc:
            # The device enumerates but cannot be opened.  Seen on Windows once
            # the camera has been handed to a VM by USB passthrough: a stub
            # with the right VID/PID stays visible to libusb on the host.
            raise RuntimeError(
                "a TS2 is visible but cannot be opened. If you passed the "
                "camera through to a virtual machine, it belongs to the guest "
                "now and is not usable on this host."
            ) from exc
        self.streaming = False

    # ---- command channel -------------------------------------------------
    def _state(self):
        return self.dev.ctrl_transfer(0xC1, 0x22, 0, 0, 1, 1000)[0]

    def command(self, group, op, params=b"", reply_len=0, timeout=1000):
        try:
            self.dev.ctrl_transfer(0x41, 0x20, 0, 0,
                                   build_cmd(group, op, params, reply_len),
                                   timeout)
        except self._usb.USBError as exc:
            if exc.errno == 13:
                raise PermissionError(PERMISSION_HELP) from exc
            raise
        deadline = time.time() + 2.0
        while time.time() < deadline:
            st = self._state()
            if st == STATE_REPLY_READY:
                break
            if st == STATE_IDLE and reply_len == 0:
                return b""
            time.sleep(0.002)
        else:
            raise RuntimeError("timeout waiting for command reply")
        data = bytes(self.dev.ctrl_transfer(0xC1, 0x21, 0, 0, reply_len, timeout))
        self._state()                      # drain back to idle
        return data

    def info(self, field):
        sel, length = INFO_FIELDS[field]
        raw = self.command(0x01, 0x81, bytes((sel,)), length)
        return raw.split(b"\0")[0].decode("ascii", "replace")

    def info_all(self):
        return {k: self.info(k) for k in INFO_FIELDS}

    def status(self):
        """One status byte from command group 0x2F.

        Reads 0x35 on a freshly enumerated device and 0x01 once it has been
        started at least once -- including while stopped.  Reported for
        information; see STATUS_FRESH.  Not a stream-state flag.
        """
        return self.command(0x2F, 0x81, b"\0", 1)[0]

    def shutter(self):
        """Trigger the mechanical shutter / flat-field correction."""
        self.command(0x36, 0x43)

    # ---- streaming -------------------------------------------------------
    def start_stream(self):
        self.dev.set_interface_altsetting(interface=VIDEO_IF,
                                          alternate_setting=VIDEO_ALT)
        try:
            self.dev.clear_halt(EP_VIDEO)
        except Exception:
            pass
        self.dev.ctrl_transfer(0x40, 0xEE, 0, 1, None, 1000)
        self.streaming = True

    def stop_stream(self):
        try:
            self.dev.set_interface_altsetting(interface=VIDEO_IF,
                                              alternate_setting=0)
        except Exception:
            pass
        self.streaming = False

    def frames(self, timeout=1500, settle=8.0):
        """Yield complete 197644-byte frames.

        Short transfers (12-byte headers, and the partial frame produced when
        the stream is joined mid-frame) are skipped.  `settle` bounds how long
        to tolerate timeouts while the module runs its start-up NUC.
        """
        last = time.time()
        while True:
            try:
                buf = bytes(self.dev.read(EP_VIDEO, FRAME_BYTES, timeout))
            except self._usb.USBError as e:
                if getattr(e, "errno", None) == 32:          # pipe stalled
                    try:
                        self.dev.clear_halt(EP_VIDEO)
                    except Exception:
                        pass
                if time.time() - last > settle:
                    raise
                continue
            if len(buf) == FRAME_BYTES:
                last = time.time()
                yield buf

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop_stream()


# ---- frame decoding ------------------------------------------------------
def parse_header(frame):
    ln, flags, ts_a, ts_b, ms = struct.unpack_from("<BBIIH", frame, HDR_OFF)
    return {"len": ln, "flags": flags, "parity": flags & 1,
            "is_trailer": bool(flags & 2), "ts_a": ts_a, "ts_b": ts_b, "ms": ms}


def preview(frame):
    """8-bit AGC'd grey image, shape (192, 256)."""
    import numpy as np
    return np.frombuffer(frame, np.uint8, IMG_LEN, IMG_OFF
                         ).reshape(HEIGHT, WIDTH, 2)[:, :, 0]


def raw16(frame):
    """Radiometric plane as uint16, shape (192, 256)."""
    import numpy as np
    return np.frombuffer(frame, "<u2", WIDTH * HEIGHT, TMP_OFF
                         ).reshape(HEIGHT, WIDTH)


def celsius(frame):
    """Radiometric plane in degrees Celsius, float32."""
    return raw16(frame).astype("float32") / 64.0 - 273.15

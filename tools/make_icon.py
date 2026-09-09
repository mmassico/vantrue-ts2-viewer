#!/usr/bin/env python3
"""Generate the vantrue-ts2-viewer application icon.

Draws the TS2 as vector-ish shapes with Pillow at 8x supersampling, then
downsamples.  Two silhouettes are offered:

    portrait    the camera stood on end (the ts2_photo1.png view)
    landscape   the camera lying flat (the ts2_photo0.png / icon_proto.png view)

Portrait wins for an icon: an app icon is a square, and a landscape camera
leaves the top and bottom thirds empty, which costs real pixels at 16x16.

The lens aperture is filled with OpenCV's INFERNO ramp -- the viewer's own
default palette -- so the icon reads as "thermal" rather than "webcam", and
picks up the app's colour identity for free.

    python make_icon.py              rewrite icon.ico and ts2_view.py's ICON_PNGS
    python make_icon.py --preview    also write a 256px render and a size sheet

Needs Pillow, which the viewer itself does not: this is a build-time tool.
"""
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

SS = 8                      # supersampling factor
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "preview")

# --- palette --------------------------------------------------------------
# The real camera is near-black, but a near-black icon disappears against a
# dark title bar.  So the body is lifted to a mid charcoal and every part gets
# a light rim, which reads as an edge highlight on light backgrounds and as
# separation on dark ones.
BODY_DARK = (56, 60, 68)
BODY_EDGE = (150, 157, 168)
SILVER = (198, 204, 212)
SILVER_EDGE = (132, 139, 149)
BARREL = (40, 43, 49)
BARREL_EDGE = (150, 157, 168)
KNURL = (104, 110, 120)
USBC = (176, 182, 191)
PRINT = (226, 230, 236)          # the white "VANTRUE POWER" printing
PRINT_DIM = (176, 182, 191)      # the smaller "TS2 Thermal Camera" line


def inferno_lut():
    """256 RGB entries of OpenCV's INFERNO colormap (0 = cold, 255 = hot)."""
    import cv2
    ramp = np.arange(256, dtype=np.uint8).reshape(1, 256)
    bgr = cv2.applyColorMap(ramp, cv2.COLORMAP_INFERNO)[0]
    return [tuple(int(c) for c in px[::-1]) for px in bgr]


LUT = inferno_lut()


def rrect(d, box, r, fill, outline=None, width=0):
    # Pillow >= 10 wants integer geometry
    d.rounded_rectangle([round(v) for v in box], radius=round(r), fill=fill,
                        outline=outline, width=int(width))


def circle(d, cx, cy, r, fill, outline=None, width=0):
    d.ellipse([round(cx - r), round(cy - r), round(cx + r), round(cy + r)],
              fill=fill, outline=outline, width=int(width))


def lens(d, cx, cy, r, detail=True):
    """The barrel, its knurled grip ring, and the thermal aperture."""
    circle(d, cx, cy, r, BARREL, outline=BARREL_EDGE, width=max(1, int(r * 0.06)))
    if detail:
        # knurling: short radial notches around the grip ring
        import math
        for i in range(32):
            a = i * math.pi / 16
            r0, r1 = r * 0.84, r * 0.98
            d.line([round(cx + r0 * math.cos(a)), round(cy + r0 * math.sin(a)),
                    round(cx + r1 * math.cos(a)), round(cy + r1 * math.sin(a))],
                   fill=KNURL, width=max(1, int(r * 0.06)))
    circle(d, cx, cy, r * 0.76, (24, 26, 30))

    # aperture: inferno ramp, hot in the middle.  Weighted towards the warm
    # end of the ramp -- a mostly-purple disc reads as "dark", not "thermal".
    ap = r * 0.62
    steps = 72
    for i in range(steps, 0, -1):
        t = i / steps                       # 1.0 at the rim, 0 at the centre
        col = LUT[int(min(255, (1.0 - t) ** 0.85 * 300))]
        circle(d, cx, cy, ap * t, col)
    circle(d, cx, cy, ap * 0.20, (255, 250, 214))
    # glass highlight: a thin crescent near the rim, not a blob in the middle.
    # The draw context is in RGBA mode, so a translucent fill blends properly.
    if detail:
        h = ap * 0.86
        d.arc([round(cx - h), round(cy - h), round(cx + h), round(cy + h)],
              start=203, end=262, fill=(255, 255, 255, 92),
              width=max(1, int(ap * 0.10)))


def draw_landscape(size, detail=True):
    """The camera lying flat, as in ts2_photo0.png.

    Layout follows the real device: the brushed-aluminium panel is a band
    across the TOP of the body, running from the lens to the right edge, and
    the two lines of white printing ("VANTRUE POWER" / "TS2 Thermal Camera")
    sit BELOW it on the black shell -- not inside the panel.
    """
    S = size * SS
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    u = S / 100.0
    edge = max(1, int(1.6 * u))

    # USB-C plug on top, just right of the lens; drawn first so the body
    # overlaps its root
    rrect(d, [40 * u, 6 * u, 57 * u, 30 * u], 4 * u, USBC,
          outline=SILVER_EDGE, width=edge)

    rrect(d, [3 * u, 25 * u, 97 * u, 81 * u], 10 * u, BODY_DARK,
          outline=BODY_EDGE, width=edge)

    # aluminium band across the top of the body
    rrect(d, [44 * u, 28 * u, 93 * u, 52 * u], 4 * u, SILVER,
          outline=SILVER_EDGE, width=max(1, int(1.0 * u)))

    # the two lines of printing, below the band on the black shell
    if detail:
        rrect(d, [49 * u, 58 * u, 89 * u, 64 * u], 2.6 * u, PRINT)
        rrect(d, [54 * u, 69 * u, 89 * u, 74 * u], 2.2 * u, PRINT_DIM)

    # lens last: it overhangs the body top and bottom, as on the real camera
    lens(d, 27 * u, 53 * u, 27 * u, detail=detail)
    return img.resize((size, size), Image.LANCZOS)


def draw_portrait(size, detail=True):
    """The camera stood on end, as in ts2_photo1.png.

    Literally the landscape artwork turned a quarter turn, which is what the
    product itself is -- so the two can never drift apart.
    """
    return draw_landscape(size, detail=detail).rotate(-90, expand=False)


DRAW = {"portrait": draw_portrait, "landscape": draw_landscape}

# below this size the knurling and the panel lines turn to mush, so drop them
DETAIL_CUTOFF = 40
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(style, size):
    return DRAW[style](size, detail=size >= DETAIL_CUTOFF)


def contact_sheet(style):
    """The same icon at the sizes that actually get used, on a light and a
    dark strip -- title bars come in both."""
    sizes = (16, 24, 32, 48, 64, 128)
    pad, gap = 12, 14
    w = pad * 2 + sum(sizes) + gap * (len(sizes) - 1)
    h = pad * 2 + 128 * 2 + gap
    sheet = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    ImageDraw.Draw(sheet).rectangle([0, pad + 128 + gap // 2, w, h],
                                    fill=(34, 36, 40, 255))
    x = pad
    for s in sizes:
        ic = render(style, s)
        sheet.paste(ic, (x, pad + 128 - s), ic)
        sheet.paste(ic, (x, pad + 128 + gap + 128 - s), ic)
        x += s + gap
    return sheet


STYLE = "landscape"          # the shipped silhouette


def emit_assets(repo):
    """Write icon.ico for Windows and the base64 PNG block for the script."""
    import base64
    import io
    frames = [render(STYLE, s) for s in ICO_SIZES]
    ico = os.path.join(repo, "icon.ico")
    frames[-1].save(ico, format="ICO",
                    sizes=[(s, s) for s in ICO_SIZES])
    print(f"  {ico}  ({', '.join(str(s) for s in ICO_SIZES)})")

    lines = ["ICON_PNGS = ("]
    # what a window manager actually picks from; bigger frames live in the
    # .ico.  Quantised to 128 colours: visually identical to full RGBA here,
    # and roughly half the base64 to carry around in the source.
    for s in (16, 32, 48, 64):
        buf = io.BytesIO()
        (render(STYLE, s)
         .quantize(colors=128, method=Image.FASTOCTREE)
         .save(buf, format="PNG", optimize=True))
        b64 = base64.b64encode(buf.getvalue()).decode()
        lines.append(f"    # {s} x {s}")
        lines.append("    (")
        for i in range(0, len(b64), 68):
            lines.append(f'        "{b64[i:i + 68]}"')
        lines.append("    ),")
    lines.append(")")
    block = chr(10).join(lines) + chr(10)
    # rewrite the ICON_PNGS block in ts2_view.py rather than leaving a
    # snippet to paste by hand
    import re
    view = os.path.join(repo, "ts2_view.py")
    text = open(view, encoding="utf-8").read()
    pattern = r"^ICON_PNGS = \(\n(?:.*\n)*?\)\n"
    new, n = re.subn(pattern, block, text, count=1, flags=re.M)
    if n != 1:
        raise SystemExit(f"could not find the ICON_PNGS block in {view}")
    open(view, "w", encoding="utf-8", newline="\n").write(new)
    print(f"  {view}  (ICON_PNGS updated, {len(block)} bytes)")


def main():
    emit_assets(REPO)
    if "--preview" in sys.argv:
        os.makedirs(OUT, exist_ok=True)
        render(STYLE, 256).save(f"{OUT}/icon_256.png")
        contact_sheet(STYLE).save(f"{OUT}/sheet.png")
        print(f"  {OUT}/icon_256.png, {OUT}/sheet.png")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Visuele controle van de schaalmodi met de ECHTE mpv op een virtueel scherm (Xvfb).

Vereist: mpv, Xvfb, xwd, ImageMagick (convert), Pillow.
Gebruik: python3 tests/mpv_visual_check.py <map met portrait.png en portrait.webm>

Gebruikt dezelfde mpv-eigenschappen als de mpv-weergave van de player (SCALE_PROPS), maar
met vo=x11 in plaats van DRM (die is op een testmachine niet beschikbaar).
"""

import io
import os
import subprocess
import sys
import time

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
from signage.player.mpv import Mpv  # noqa: E402
from signage.player.mpv_renderer import SCALE_PROPS  # noqa: E402

MEDIA = sys.argv[1]
W, H = 1280, 720
os.environ["DISPLAY"] = ":97"
xvfb = subprocess.Popen(["Xvfb", ":97", "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
RED, BLUE, GREEN, BLACK = (220, 30, 30), (30, 60, 220), (30, 180, 60), (0, 0, 0)


def near(px, rgb, tol=60):
    if rgb in (BLUE, GREEN):   # videocodecs verschuiven kleuren; kijk naar het dominante kanaal
        ch = 2 if rgb == BLUE else 1
        return px[ch] > 90 and px[ch] > 1.5 * max(v for i, v in enumerate(px) if i != ch)
    return all(abs(a - b) <= tol for a, b in zip(px, rgb))


def grab():
    xwd = subprocess.run(["xwd", "-root", "-silent", "-display", ":97"], capture_output=True, check=True).stdout
    png = subprocess.run(["convert", "xwd:-", "png:-"], input=xwd, capture_output=True, check=True).stdout
    return Image.open(io.BytesIO(png)).convert("RGB")


ok = True
try:
    m = Mpv("mpv", "/tmp/mpv-visual.sock", vo="x11", gpu_context="", hwdec="no",
            extra_args="--ao=null --geometry=0:0 --autofit=100%x100%")
    m.start()
    for name in ("portrait.png", "portrait.webm"):
        for mode in ("cover", "contain", "fill"):
            for k, v in SCALE_PROPS[mode].items():
                m.set(k, v)
            m.command("loadfile", os.path.join(MEDIA, name), "replace")
            m.set("pause", False)
            time.sleep(1.2)
            img = grab()
            c = [img.getpixel(p) for p in ((3, 3), (W - 4, 3), (3, H - 4), (W - 4, H - 4))]
            mid = img.getpixel((W // 2, H // 2))
            if mode == "cover":
                res = all(near(x, RED) for x in c) and near(mid, RED)
            elif mode == "contain":
                res = all(near(x, BLACK, 20) for x in c) and near(img.getpixel((W // 2, 3)), BLUE)
            else:
                res = near(c[0], BLUE) and near(c[3], GREEN) and near(mid, RED)
            ok &= res
            print(f"{'OK  ' if res else 'FAIL'} {name:14s} {mode:8s} hoeken={c} midden={mid}")
            img.save(f"/tmp/mpv-visual-{name}-{mode}.png")
    m.stop()
finally:
    xvfb.terminate()
sys.exit(0 if ok else 1)

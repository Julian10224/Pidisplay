#!/usr/bin/env python3
"""Visuele controle van het logo in de mpv-weergave met de ECHTE mpv op Xvfb.

Vereist: mpv, Xvfb, xwd, ImageMagick (convert), Pillow.
Gebruik: python3 tests/mpv_logo_check.py
"""

import io
import os
import subprocess
import sys
import tempfile
import time

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
from signage.player.mpv import Mpv  # noqa: E402
from signage.player.mpv_renderer import MpvRenderer  # noqa: E402

W, H = 1280, 720
os.environ["DISPLAY"] = ":98"
xvfb = subprocess.Popen(["Xvfb", ":98", "-screen", "0", f"{W}x{H}x24", "-nolisten", "tcp"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.5)
tmp = tempfile.mkdtemp()
bg = os.path.join(tmp, "bg.png")
Image.new("RGB", (1920, 1080), (30, 60, 220)).save(bg)
logo = os.path.join(tmp, "logo.png")
lg = Image.new("RGBA", (400, 200), (255, 255, 0, 255))   # geel, ondoorzichtig
lg.paste((255, 255, 0, 0), (0, 100, 400, 200))           # onderste helft transparant
lg.save(logo)


def grab():
    xwd = subprocess.run(["xwd", "-root", "-silent", "-display", ":98"], capture_output=True, check=True).stdout
    png = subprocess.run(["convert", "xwd:-", "png:-"], input=xwd, capture_output=True, check=True).stdout
    return Image.open(io.BytesIO(png)).convert("RGB")


class R(MpvRenderer):
    """Alleen de logo-functies van de renderer, met een eigen mpv-proces."""
    def __init__(self, rotation):  # noqa: D401
        self.cache_dir = tmp
        self.cfg = {"player": {"rotation": rotation}}
        self.overlay = None
        self.screen_size = None
        self.errors, self.warned = [], set()
        self.lock = __import__("threading").Lock()
        self.mpv = Mpv("mpv", os.path.join(tmp, "s.sock"), vo="x11", gpu_context="", hwdec="no",
                       extra_args="--ao=null --geometry=0:0 --autofit=100%x100%")


ok = True
try:
    for rot, corner in ((0, (0, 0)), (90, (1, 0))):
        r = R(rot)
        r.mpv.start()
        r.mpv.command("loadfile", bg, "replace")
        time.sleep(0.8)
        r.set_logo({"media_id": "L", "local": logo})
        time.sleep(0.6)
        img = grab()
        img.save(f"/tmp/mpv-logo-{rot}.png")
        sw, sh = r._screen()
        m = round(0.03 * min(sw, sh))
        if rot == 0:
            yellow = img.getpixel((m + 20, m + 10))         # bovenste helft logo
            through = img.getpixel((m + 20, m + 60))        # transparante helft: achtergrond
            outside = img.getpixel((m + 260, m + 10))       # naast het logo (max 18% = 230 px breed)
        else:  # 90°: logo rechtsboven, gedraaid; ondoorzichtige helft aan de rechterkant
            yellow = img.getpixel((sw - m - 10, m + 20))
            through = img.getpixel((sw - m - 60, m + 20))
            outside = img.getpixel((sw - m - 10, m + 260))
        res = (yellow[0] > 200 and yellow[1] > 200 and yellow[2] < 80 and through[2] > 150 and through[0] < 80
               and outside[2] > 150)
        print(f"{'OK  ' if res else 'FAIL'} rotatie {rot}: scherm {sw}x{sh} logo={yellow} transparant={through} naast={outside}")
        ok &= res
        r.set_logo(None)
        time.sleep(0.5)
        img = grab()
        gone = img.getpixel((m + 20, m + 10)) if rot == 0 else img.getpixel((sw - m - 10, m + 20))
        res2 = gone[2] > 150 and gone[0] < 80
        print(f"{'OK  ' if res2 else 'FAIL'} rotatie {rot}: logo weg na verwijderen {gone}")
        ok &= res2
        r.mpv.stop()
finally:
    xvfb.terminate()
sys.exit(0 if ok else 1)

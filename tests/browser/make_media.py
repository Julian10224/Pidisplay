"""Testmedia voor de browserplayertests (vereist Pillow en ffmpeg met libvpx-vp9)."""

import os
import subprocess
import sys

from PIL import Image, ImageDraw

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), ".media")
os.makedirs(OUT, exist_ok=True)

RED, BLUE, GREEN = (220, 30, 30), (30, 60, 220), (30, 180, 60)


def banded(w, h, path):
    """Staand of liggend beeld: rood midden, blauwe band boven, groene band onder."""
    img = Image.new("RGB", (w, h), RED)
    d = ImageDraw.Draw(img)
    band = h // 10
    d.rectangle([0, 0, w, band], fill=BLUE)
    d.rectangle([0, h - band, w, h], fill=GREEN)
    img.save(path)


banded(1080, 1920, os.path.join(OUT, "portrait.png"))
Image.new("RGB", (1920, 1080), (40, 200, 120)).save(os.path.join(OUT, "landscape.jpg"), quality=90)
Image.new("RGB", (320, 180), (240, 220, 30)).save(os.path.join(OUT, "small.png"))
Image.new("RGB", (1920, 1080), (17, 24, 39)).save(os.path.join(OUT, "standby.png"))


def video(path, color, size, seconds, bands=False):
    if os.path.exists(path):
        return
    vf = f"color=c={color}:s={size}:r=25:d={seconds}"
    filt = []
    if bands:
        w, h = map(int, size.split("x"))
        b = h // 10
        filt = ["-vf", f"drawbox=x=0:y=0:w={w}:h={b}:color=blue:t=fill,drawbox=x=0:y={h - b}:w={w}:h={b}:color=green:t=fill"]
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", vf, *filt,
                    "-c:v", "libvpx-vp9", "-b:v", "200k", "-deadline", "realtime", "-pix_fmt", "yuv420p", path],
                   check=True)


video(os.path.join(OUT, "portrait.webm"), "0xdc1e1e", "360x640", 2, bands=True)
video(os.path.join(OUT, "landscape.webm"), "0x1ec8dc", "640x360", 1.5)
video(os.path.join(OUT, "transition.webm"), "0xdc1edc", "640x360", 1.2)
video(os.path.join(OUT, "short.webm"), "0x20c020", "640x360", 0.2)

html = os.path.join(OUT, "html")
os.makedirs(os.path.join(html, "page", "css"), exist_ok=True)
with open(os.path.join(html, "page", "index.html"), "w") as fh:
    fh.write('<!doctype html><html><head><link rel="stylesheet" href="css/s.css"></head>'
             '<body><div id="t">HTML</div></body></html>')
with open(os.path.join(html, "page", "css", "s.css"), "w") as fh:
    fh.write("html,body{margin:0;width:100vw;height:100vh;overflow:hidden;background:rgb(250,140,20)}"
             "#t{font:20vmin sans-serif;color:#fff;text-align:center;padding-top:30vh}")
os.makedirs(os.path.join(html, "hang"), exist_ok=True)
with open(os.path.join(html, "hang", "index.html"), "w") as fh:
    fh.write('<!doctype html><body style="margin:0;background:rgb(120,20,200)">'
             '<script>setTimeout(()=>{for(;;){}},300)</script></body>')
os.makedirs(os.path.join(html, "design"), exist_ok=True)
with open(os.path.join(html, "design", "index.html"), "w") as fh:
    fh.write('<!doctype html><body style="margin:0;width:800px;height:800px;overflow:hidden;'
             'background:rgb(20,120,250)"></body>')
os.makedirs(os.path.join(html, "external"), exist_ok=True)
with open(os.path.join(html, "external", "index.html"), "w") as fh:
    fh.write('<!doctype html><head><link rel="stylesheet" href="https://cdn.example.invalid/x.css">'
             '<script src="https://cdn.example.invalid/x.js"></script></head>'
             '<body style="margin:0;background:rgb(60,60,60)"></body>')
print(OUT)

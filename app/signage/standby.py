"""Standby-scherm dat de player toont als er niets af te spelen valt.

Het scherm toont de systeemnaam, hoe je de beheerwebsite bereikt en, zolang er nog geen
beheerder is aangemaakt, de eenmalige setup-code. Wie fysiek bij het scherm staat kan
daarmee de installatie afronden; iemand anders op het netwerk niet.
"""

import hashlib
import os

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = {
    "bold": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"],
    "regular": ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
}


def _font(kind, size):
    for path in FONT_CANDIDATES[kind]:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size)  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def _center(draw, y, text, font, fill, width):
    box = draw.textbbox((0, 0), text, font=font)
    w = box[2] - box[0]
    draw.text(((width - w) / 2, y), text, font=font, fill=fill)
    return y + (box[3] - box[1])


def render(path, system_name, urls, setup_code=None, message=None, size=(1920, 1080)):
    """Teken het standby-scherm; schrijft atomair en retourneert de sha256."""
    w, h = size
    img = Image.new("RGB", size, (17, 24, 39))
    draw = ImageDraw.Draw(img)
    # Subtiele accentbalk
    draw.rectangle([0, h - 12, w, h], fill=(56, 189, 248))

    y = 300
    y = _center(draw, y, system_name or "Digital Signage", _font("bold", 88), (243, 244, 246), w) + 40
    y = _center(draw, y, message or "Er is geen content om af te spelen.", _font("regular", 44),
                (156, 163, 175), w) + 90
    small = _font("regular", 38)
    if urls:
        y = _center(draw, y, "Beheer via een apparaat op hetzelfde netwerk:", small, (209, 213, 219), w) + 24
        for url in urls[:3]:
            y = _center(draw, y, url, _font("bold", 44), (125, 211, 252), w) + 18
    else:
        y = _center(draw, y, "Geen netwerkverbinding gevonden.", small, (252, 165, 165), w) + 18
    if setup_code:
        y += 50
        y = _center(draw, y, "Setup-code voor het aanmaken van de beheerder:", small, (209, 213, 219), w) + 20
        _center(draw, y, setup_code, _font("bold", 96), (250, 204, 21), w)

    tmp = path + ".tmp"
    img.save(tmp, "PNG")
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()

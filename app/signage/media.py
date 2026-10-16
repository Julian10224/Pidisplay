"""Mediavalidatie, afspeelversies (renditions) en thumbnails.

Afbeeldingen krijgen altijd een afspeelversie: EXIF-rotatie toegepast en verkleind tot
maximaal het ingestelde formaat (standaard 1920x1080). De GPU van de Pi 3B+ (VideoCore IV)
kan geen texturen groter dan 2048x2048 verwerken, dus grotere originelen worden nooit
direct aan de player gegeven.

Video's worden ongewijzigd afgespeeld. ffprobe controleert of het bestand echt een video
is; codecs die de Pi 3B+ niet hardwarematig decodeert leveren een waarschuwing op.
"""

import hashlib
import json
import os
import re
import subprocess
import unicodedata

from PIL import Image, ImageOps

IMAGE_EXTS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm"}
HTML_EXTS = {".html", ".htm", ".zip"}
ALLOWED_EXTS = set(IMAGE_EXTS) | VIDEO_EXTS | HTML_EXTS

MIME_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
    ".mkv": "video/x-matroska", ".webm": "video/webm",
    ".html": "text/html", ".htm": "text/html", ".zip": "application/zip",
}

THUMB_SIZE = (480, 270)


class MediaError(Exception):
    """Ongeldig of niet-ondersteund bestand (melding is voor de gebruiker bedoeld)."""


def safe_display_name(filename):
    """Maak van een geüploade bestandsnaam een nette weergavenaam (zonder pad)."""
    name = os.path.basename((filename or "").replace("\\", "/"))
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    stem = os.path.splitext(name)[0].strip() or "bestand"
    return stem[:120]


def file_ext(filename):
    return os.path.splitext(os.path.basename(filename or ""))[1].lower()


def sha256_file(path, chunk=1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _fsync_file(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Afbeeldingen
# ---------------------------------------------------------------------------

def inspect_image(path, ext, max_pixels):
    expected = IMAGE_EXTS[ext]
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with Image.open(path) as img:
            fmt = img.format
            width, height = img.size
            img.verify()
    except Image.DecompressionBombError:
        raise MediaError("Afbeelding is te groot (te veel pixels).")
    except Exception:
        raise MediaError("Bestand is geen geldige afbeelding of is beschadigd.")
    if fmt != expected:
        raise MediaError(f"Bestandsinhoud ({fmt}) komt niet overeen met de extensie {ext}.")
    if width < 1 or height < 1:
        raise MediaError("Afbeelding heeft geen geldige afmetingen.")
    if width * height > max_pixels:
        raise MediaError(
            f"Afbeelding is {width}x{height} pixels; maximaal {max_pixels // 1_000_000} megapixel "
            "wordt ondersteund op deze hardware.")
    return {"kind": "image", "format": fmt, "width": width, "height": height,
            "duration": None, "codec": fmt.lower(), "warnings": []}


MAX_TEXTURE_SIDE = 2048   # VideoCore IV (Pi 3) kan geen grotere texturen verwerken


def rendition_size(w, h, screen_w, screen_h, max_side=MAX_TEXTURE_SIDE):
    """Afmeting van de afspeelversie.

    De afspeelversie is groot genoeg om het scherm beeldvullend (cover) te bedekken:
    de kortste schaal bepaalt, zodat een staande foto op een liggend scherm niet wazig
    wordt na het bijsnijden. Nooit vergroten, en nooit groter dan 2048 px per zijde.
    """
    scale = min(1.0, max(screen_w / w, screen_h / h))
    nw, nh = w * scale, h * scale
    if max(nw, nh) > max_side:
        f = max_side / max(nw, nh)
        nw, nh = nw * f, nh * f
    return max(1, round(nw)), max(1, round(nh))


def make_image_rendition(src, dst_base, max_size):
    """Schrijf afspeelversie; retourneert (pad, breedte, hoogte)."""
    with Image.open(src) as img:
        if img.format == "JPEG":
            # Snellere en zuinigere decodering op de Pi bij grote JPEG's.
            img.draft("RGB", (MAX_TEXTURE_SIDE, MAX_TEXTURE_SIDE))
        img.load()
        img = ImageOps.exif_transpose(img)
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        target = rendition_size(img.size[0], img.size[1], max_size[0], max_size[1])
        if target != img.size:
            img = img.resize(target, Image.LANCZOS)
        if has_alpha:
            img = img.convert("RGBA")
            dst = dst_base + ".png"
            img.save(dst + ".tmp", "PNG", optimize=False)
        else:
            img = img.convert("RGB")
            dst = dst_base + ".jpg"
            img.save(dst + ".tmp", "JPEG", quality=92, subsampling=0)
        size = img.size
    _fsync_file(dst + ".tmp")
    os.replace(dst + ".tmp", dst)
    return dst, size[0], size[1]


def make_image_thumb(src, dst):
    with Image.open(src) as img:
        if img.format == "JPEG":
            img.draft("RGB", (THUMB_SIZE[0] * 2, THUMB_SIZE[1] * 2))
        img.load()
        img = ImageOps.exif_transpose(img)
        img.thumbnail(THUMB_SIZE, Image.LANCZOS)
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (32, 36, 44))
            rgba = img.convert("RGBA")
            bg.paste(rgba, mask=rgba.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
        img.save(dst + ".tmp", "JPEG", quality=80)
    os.replace(dst + ".tmp", dst)


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------

def ffprobe(path, timeout=60):
    try:
        res = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise MediaError("ffprobe is niet geïnstalleerd; video's kunnen niet worden gecontroleerd.")
    except subprocess.TimeoutExpired:
        raise MediaError("Controle van de video duurde te lang; het bestand is mogelijk beschadigd.")
    if res.returncode != 0:
        raise MediaError("Bestand is geen geldige video of is beschadigd.")
    try:
        return json.loads(res.stdout.decode("utf-8", "replace"))
    except ValueError:
        raise MediaError("Bestand is geen geldige video.")


def _fps(stream):
    for key in ("avg_frame_rate", "r_frame_rate"):
        val = stream.get(key) or "0/0"
        try:
            num, den = val.split("/")
            if float(den) > 0:
                return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            continue
    return 0.0


def inspect_video(path):
    info = ffprobe(path)
    streams = info.get("streams") or []
    fmt = info.get("format") or {}
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not video:
        raise MediaError("Bestand bevat geen videostream.")
    v = video[0]
    codec = (v.get("codec_name") or "onbekend").lower()
    width, height = int(v.get("width") or 0), int(v.get("height") or 0)
    if width < 1 or height < 1:
        raise MediaError("Video heeft geen geldige afmetingen.")
    duration = None
    for candidate in (fmt.get("duration"), v.get("duration")):
        try:
            if candidate is not None and float(candidate) > 0:
                duration = float(candidate)
                break
        except ValueError:
            continue

    warnings = []
    if codec != "h264":
        warnings.append(f"Codec '{codec}' wordt op de Raspberry Pi 3B+ niet hardwarematig gedecodeerd; "
                        "afspelen kan haperen. Gebruik H.264.")
    else:
        profile = (v.get("profile") or "").lower()
        pix = (v.get("pix_fmt") or "").lower()
        if "10" in profile or "10" in pix or "444" in profile or "422" in profile:
            warnings.append(f"H.264-profiel '{v.get('profile')}' ({pix}) wordt niet hardwarematig ondersteund; "
                            "gebruik 8-bit High/Main profiel (yuv420p).")
    if width > 1920 or height > 1080:
        warnings.append(f"Resolutie {width}x{height} is hoger dan 1920x1080; de Pi 3B+ speelt dit niet vloeiend af.")
    fps = _fps(v)
    if fps > 31 and height > 720:
        warnings.append(f"{fps:.0f} fps bij {height}p is te zwaar voor de Pi 3B+; gebruik maximaal 30 fps.")
    if audio:
        acodec = (audio[0].get("codec_name") or "").lower()
        if acodec not in ("aac", "mp3", "mp2", "ac3", "opus", "vorbis", "pcm_s16le"):
            warnings.append(f"Audiocodec '{acodec}' wordt mogelijk niet afgespeeld; gebruik AAC.")
    if duration is None:
        warnings.append("Duur van de video kon niet worden bepaald.")
    return {"kind": "video", "format": (fmt.get("format_name") or "video").split(",")[0],
            "width": width, "height": height, "duration": duration, "codec": codec,
            "warnings": warnings}


def make_video_thumb(src, dst, duration):
    seek = 1.0
    if duration:
        seek = min(1.0, duration / 2)
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{seek:.2f}", "-i", src, "-frames:v", "1",
           "-vf", f"scale={THUMB_SIZE[0]}:{THUMB_SIZE[1]}:force_original_aspect_ratio=decrease",
           "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "-y", dst + ".tmp"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=90, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if res.returncode != 0 or not os.path.exists(dst + ".tmp") or os.path.getsize(dst + ".tmp") == 0:
        try:
            os.unlink(dst + ".tmp")
        except OSError:
            pass
        return False
    os.replace(dst + ".tmp", dst)
    return True


# ---------------------------------------------------------------------------
# Volledige verwerking van een binnengekomen bestand
# ---------------------------------------------------------------------------

def process_incoming(cfg, part_path, original_filename, media_id):
    """Valideer en verwerk een volledig ontvangen upload.

    Retourneert een dict met kolommen voor de media-tabel. Bij een fout worden alle
    tussenbestanden opgeruimd en wordt MediaError gegooid. Het definitieve bestand
    wordt pas in files/ gezet nadat alle controles geslaagd zijn.
    """
    ext = file_ext(original_filename)
    if ext not in ALLOWED_EXTS:
        raise MediaError(f"Bestandstype '{ext or 'onbekend'}' wordt niet ondersteund. "
                         "Toegestaan: PNG, JPEG, WebP, MP4, M4V, MOV, MKV, WebM, HTML en ZIP (HTML-project).")
    if ext in HTML_EXTS:
        from . import htmlpkg
        try:
            return htmlpkg.process_html_upload(cfg, part_path, original_filename, media_id)
        except htmlpkg.HtmlError as exc:
            raise MediaError(str(exc))
    created = []
    try:
        if ext in IMAGE_EXTS:
            meta = inspect_image(part_path, ext, cfg.max_image_pixels)
            rend, rw, rh = make_image_rendition(part_path, os.path.join(cfg.renditions_dir, media_id),
                                                cfg.max_image_size)
            created.append(rend)
            thumb = os.path.join(cfg.thumbs_dir, media_id + ".jpg")
            make_image_thumb(rend, thumb)
            created.append(thumb)
            playback_dir, playback_name = "renditions", os.path.basename(rend)
            playback_path = rend
            thumb_name = os.path.basename(thumb)
        else:
            meta = inspect_video(part_path)
            thumb = os.path.join(cfg.thumbs_dir, media_id + ".jpg")
            thumb_name = os.path.basename(thumb) if make_video_thumb(part_path, thumb, meta["duration"]) else None
            if thumb_name:
                created.append(thumb)
            playback_dir, playback_name = "files", media_id + ext
            playback_path = None

        final_name = media_id + ext
        final_path = os.path.join(cfg.files_dir, final_name)
        _fsync_file(part_path)
        os.replace(part_path, final_path)
        created.append(final_path)
        if playback_path is None:
            playback_path = final_path
        result = {
            "kind": meta["kind"], "format": meta["format"], "ext": ext,
            "width": meta["width"], "height": meta["height"], "duration": meta["duration"],
            "codec": meta["codec"],
            "status": "warning" if meta["warnings"] else "ok",
            "warning": " ".join(meta["warnings"]) or None,
            "file_name": final_name, "playback_name": playback_name, "playback_dir": playback_dir,
            "playback_sha256": sha256_file(playback_path),
            "playback_size": os.path.getsize(playback_path),
            "thumb_name": thumb_name, "html_entry": None,
        }
        result["_created"] = created
        return result
    except Exception:
        for p in created:
            try:
                os.unlink(p)
            except OSError:
                pass
        raise


def media_paths(cfg, row):
    """Absolute paden van een media-record.

    Voor HTML-projecten is 'playback' de projectmap; voor ingebouwde pagina's None
    (die worden door de software zelf geleverd).
    """
    pdir = row["playback_dir"]
    if pdir == "builtin":
        playback = None
    else:
        base = {"files": cfg.files_dir, "renditions": cfg.renditions_dir, "html": cfg.html_dir}[pdir]
        playback = os.path.join(base, row["playback_name"])
    return {
        "file": os.path.join(cfg.files_dir, row["file_name"]) if row["file_name"] else None,
        "playback": playback,
        "thumb": os.path.join(cfg.thumbs_dir, row["thumb_name"]) if row["thumb_name"] else None,
    }


def playback_available(cfg, row):
    """Is het afspeelbare bestand (of de HTML-map) aanwezig?"""
    if row["playback_dir"] == "builtin":
        from . import htmlpkg
        return row["playback_name"] in htmlpkg.BUILTIN_PAGES
    p = media_paths(cfg, row)["playback"]
    if row["playback_dir"] == "html":
        return os.path.isfile(os.path.join(p, row["html_entry"] or "index.html"))
    return os.path.exists(p)

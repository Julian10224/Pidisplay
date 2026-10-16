"""Video's geschikt maken voor de Raspberry Pi 3B+ (afspeelversie).

De Pi 3B+ decodeert alleen H.264 (8-bit, 4:2:0) in hardware, tot ongeveer 1080p30. Video's
in HEVC/H.265 (standaard op iPhones), 4K, 50/60 fps, 10-bit of met een zeer hoge bitrate
haperen daardoor. Zulke video's krijgen op de achtergrond een afspeelversie:

  H.264 High, passend verkleind tot 1920x1080 (staand: 1080x1920), maximaal 30 fps
  (50/60 fps wordt 25/30), 8-bit 4:2:0, maximaal 8 Mbit/s, AAC-geluid, 'faststart'.

Het origineel blijft bewaard en downloadbaar; tot de afspeelversie klaar is speelt het
origineel. Het omzetten draait met de laagste prioriteit (nice/ionice), zodat het afspelen
voorgaat. Eerst wordt de hardware-encoder van de Pi geprobeerd (h264_v4l2m2m, snel); lukt
dat niet of is het resultaat niet goed, dan libx264 (langzamer, altijd beschikbaar).

Resultaten staan in <data_dir>/video-optimize.json, zodat een video niet bij elke start
opnieuw wordt bekeken. Een mislukte omzetting wordt niet automatisch herhaald; dat kan
met de knop in de beheerwebsite.
"""

import json
import logging
import os
import shutil
import subprocess
import threading
import time

from . import db
from . import media as media_mod

log = logging.getLogger("signage.videoopt")

MAX_LONG, MAX_SHORT = 1920, 1080
MAX_FPS = 30.5
MAX_BITRATE = 20_000_000
TARGET_BITRATE = "8M"
STALL_TIMEOUT = 180        # zo lang geen voortgang => afbreken


def _rotation(stream):
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(float(sd["rotation"])) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int((stream.get("tags") or {}).get("rotate", 0)) % 360
    except (TypeError, ValueError):
        return 0


def analyse(info):
    """Redenen waarom een video een afspeelversie nodig heeft (lege lijst = niet nodig)."""
    streams = info.get("streams") or []
    fmt = info.get("format") or {}
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    if not video:
        return []
    v = video[0]
    reasons = []
    codec = (v.get("codec_name") or "").lower()
    profile = (v.get("profile") or "").lower()
    pix = (v.get("pix_fmt") or "").lower()
    if codec != "h264":
        reasons.append({"hevc": "HEVC/H.265"}.get(codec, codec.upper() or "onbekende codec"))
    elif "10" in profile or "10" in pix or "444" in profile or "422" in profile or ("yuv4" in pix and pix != "yuv420p" and pix != "yuvj420p"):
        reasons.append(f"{v.get('profile') or pix}")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    if max(w, h) > MAX_LONG or min(w, h) > MAX_SHORT:
        reasons.append(f"{w}x{h}")
    fps = media_mod._fps(v)
    if fps > MAX_FPS:
        reasons.append(f"{fps:.0f} fps")
    try:
        bitrate = int(v.get("bit_rate") or fmt.get("bit_rate") or 0)
    except (TypeError, ValueError):
        bitrate = 0
    if bitrate > MAX_BITRATE:
        reasons.append(f"{bitrate / 1e6:.0f} Mbit/s")
    if (v.get("field_order") or "progressive") not in ("progressive", "unknown"):
        reasons.append("interlaced")
    return reasons


def _target(info):
    v = [s for s in info["streams"] if s.get("codec_type") == "video"
         and not (s.get("disposition") or {}).get("attached_pic")][0]
    w, h = int(v["width"]), int(v["height"])
    if _rotation(v) in (90, 270):        # ffmpeg draat automatisch; afmetingen na het draaien
        w, h = h, w
    box = (MAX_SHORT, MAX_LONG) if h > w else (MAX_LONG, MAX_SHORT)
    scale = min(1.0, box[0] / w, box[1] / h)
    tw, th = max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)
    fps = media_mod._fps(v) or 25.0
    out_fps = None
    if fps > MAX_FPS:
        out_fps = fps / 2 if fps / 2 <= MAX_FPS else 30
    return tw, th, out_fps or fps, out_fps is not None


def build_cmd(src, dst, info, encoder):
    tw, th, fps, change_fps = _target(info)
    vf = [f"scale={tw}:{th}:flags=bicubic"]
    if change_fps:
        vf.append(f"fps={fps:.3f}")
    vf.append("format=yuv420p")
    gop = str(max(12, int(round(fps * 2))))
    audio = [s for s in info.get("streams") or [] if s.get("codec_type") == "audio"]
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", src,
           "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "-1",
           "-vf", ",".join(vf)]
    if encoder == "h264_v4l2m2m":
        cmd += ["-c:v", "h264_v4l2m2m", "-b:v", TARGET_BITRATE, "-g", gop, "-num_capture_buffers", "16"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high", "-level:v", "4.1",
                "-crf", "22", "-maxrate", TARGET_BITRATE, "-bufsize", "16M", "-g", gop, "-threads", "3"]
    if audio:
        if (audio[0].get("codec_name") or "").lower() == "aac":
            cmd += ["-c:a", "copy"]
        else:
            cmd += ["-c:a", "aac", "-b:a", "160k", "-ac", "2"]
    cmd += ["-movflags", "+faststart", "-f", "mp4", "-progress", "pipe:1", "-nostats", dst]
    return cmd


_hw = {"checked": False, "ok": False}


def hw_encoder_available():
    """h264_v4l2m2m in ffmpeg én een encoderapparaat van de Pi (bcm2835-codec)."""
    if not _hw["checked"]:
        _hw["checked"] = True
        try:
            out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True,
                                 timeout=20).stdout
            _hw["ok"] = "h264_v4l2m2m" in out and os.path.exists("/dev/video11")
        except (OSError, subprocess.TimeoutExpired):
            _hw["ok"] = False
    return _hw["ok"]


def _low_priority():
    pre = ["nice", "-n", "19"] if shutil.which("nice") else []
    if shutil.which("ionice"):
        pre += ["ionice", "-c", "3"]
    return pre


def transcode(src, dst, info, encoder, progress=None, should_stop=None):
    """Zet src om naar dst (mp4). Geeft True terug als het gelukt én gecontroleerd is."""
    duration = float((info.get("format") or {}).get("duration") or 0) or None
    tmp = dst + ".tmp"
    cmd = _low_priority() + build_cmd(src, tmp, info, encoder)
    log.info("Video omzetten (%s): %s", encoder, os.path.basename(src))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    last = time.time()
    err_lines = []

    def read_err():
        for line in proc.stderr:
            err_lines.append(line.strip())
            del err_lines[:-20]
    threading.Thread(target=read_err, daemon=True).start()
    try:
        for line in proc.stdout:
            if should_stop and should_stop():
                proc.kill()
                break
            key, _, val = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and duration:
                try:
                    pos = int(val) / 1e6
                    last = time.time()
                    if progress:
                        progress(max(0.0, min(0.99, pos / duration)))
                except ValueError:
                    pass
            elif key == "progress" and val == "end" and progress:
                progress(1.0)
            if time.time() - last > STALL_TIMEOUT:
                proc.kill()
                err_lines.append("geen voortgang meer")
                break
        proc.wait(timeout=60)
    except Exception:
        proc.kill()
        raise
    finally:
        if proc.poll() is None:
            proc.kill()
    if proc.returncode != 0 or not os.path.exists(tmp):
        _unlink(tmp)
        raise RuntimeError((err_lines[-1] if err_lines else f"ffmpeg stopte met code {proc.returncode}")[:300])
    # Controle: echte H.264-video, afmetingen binnen de grenzen, duur klopt.
    try:
        out = media_mod.ffprobe(tmp)
    except media_mod.MediaError as exc:
        _unlink(tmp)
        raise RuntimeError(str(exc))
    v = [s for s in out.get("streams") or [] if s.get("codec_type") == "video"]
    out_dur = float((out.get("format") or {}).get("duration") or 0)
    ok = (v and (v[0].get("codec_name") or "") == "h264"
          and max(int(v[0]["width"]), int(v[0]["height"])) <= MAX_LONG
          and min(int(v[0]["width"]), int(v[0]["height"])) <= MAX_SHORT
          and (not duration or abs(out_dur - duration) <= max(1.0, 0.03 * duration)))
    if not ok:
        _unlink(tmp)
        raise RuntimeError("het resultaat voldoet niet (codec, afmetingen of duur)")
    os.replace(tmp, dst)
    return True


def _unlink(p):
    try:
        os.unlink(p)
    except OSError:
        pass


class Optimizer:
    """Wachtrij en achtergrondthread voor het omzetten van video's."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.path = os.path.join(cfg.data_dir, "video-optimize.json")
        self.lock = threading.Lock()
        self.queue = []
        self.current = None          # {"id", "progress", "encoder", "started"}
        self.stop_ids = set()
        self.wake = threading.Event()
        self.results = self._load()

    # -- toestand ------------------------------------------------------------------
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.results, fh)
        os.replace(tmp, self.path)

    def info(self, media_id):
        """Voor de beheerwebsite: wachtrij, bezig (met voortgang) of het resultaat."""
        with self.lock:
            if self.current and self.current["id"] == media_id:
                if not self.current.get("encoder"):        # nog aan het bekijken (ffprobe)
                    return {"state": "queued", "position": 0}
                return {"state": "running", "progress": round(self.current["progress"], 3),
                        "reasons": self.current.get("reasons") or []}
            if media_id in self.queue:
                return {"state": "queued", "position": self.queue.index(media_id) + 1}
            res = self.results.get(media_id)
        if not res:
            return None
        return {"state": res.get("result"), "reasons": res.get("reasons") or [],
                "message": res.get("message"), "seconds": res.get("seconds"), "encoder": res.get("encoder")}

    def enabled(self):
        conn = db.connect(self.cfg.db_path)
        try:
            return bool(db.get_settings(conn).get("optimize_videos", True))
        finally:
            conn.close()

    # -- wachtrij ------------------------------------------------------------------
    def enqueue(self, media_id, force=False):
        with self.lock:
            if force:
                self.results.pop(media_id, None)
            if media_id not in self.queue and not (self.current and self.current["id"] == media_id) \
                    and media_id not in self.results:
                self.queue.append(media_id)
        self.wake.set()

    def forget(self, media_id):
        """Bij verwijderen: uit de wachtrij, lopende omzetting stoppen."""
        with self.lock:
            if media_id in self.queue:
                self.queue.remove(media_id)
            if self.current and self.current["id"] == media_id:
                self.stop_ids.add(media_id)
            if self.results.pop(media_id, None) is not None:
                self._save()

    def scan(self):
        """Alle video's die nog niet bekeken zijn in de wachtrij zetten."""
        conn = db.connect(self.cfg.db_path)
        try:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM media WHERE kind = 'video' AND playback_dir = 'files' ORDER BY created_at")]
        finally:
            conn.close()
        for mid in ids:
            self.enqueue(mid)

    # -- verwerken -----------------------------------------------------------------
    def process(self, media_id):
        """Eén video bekijken en zo nodig omzetten (synchroon)."""
        cfg = self.cfg
        conn = db.connect(cfg.db_path)
        try:
            row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
            if row is None or row["kind"] != "video" or row["playback_dir"] != "files":
                return
            src = os.path.join(cfg.files_dir, row["file_name"])
            try:
                info = media_mod.ffprobe(src)
            except media_mod.MediaError as exc:
                self._result(media_id, "failed", [], str(exc))
                return
            reasons = analyse(info)
            if not reasons:
                self._result(media_id, "not_needed", [], None)
                return
            dst = os.path.join(cfg.renditions_dir, media_id + ".mp4")
            need = max(os.path.getsize(src) // 2, int(float((info.get("format") or {}).get("duration") or 60) * 1.2e6))
            if shutil.disk_usage(cfg.media_dir).free - need < cfg.min_free_bytes:
                self._result(media_id, "failed", reasons, "Te weinig vrije ruimte voor de afspeelversie.")
                return
            started = time.time()
            encoders = (["h264_v4l2m2m"] if hw_encoder_available() else []) + ["libx264"]
            error = None
            for enc in encoders:
                with self.lock:
                    self.current = {"id": media_id, "progress": 0.0, "encoder": enc, "started": started,
                                    "reasons": reasons}
                try:
                    transcode(src, dst, info, enc, progress=self._progress,
                              should_stop=lambda: media_id in self.stop_ids)
                    error = None
                    break
                except Exception as exc:  # noqa: BLE001
                    error = f"{enc}: {exc}"
                    log.warning("Omzetten van '%s' met %s mislukt: %s", row["name"], enc, exc)
                    if media_id in self.stop_ids:
                        break
            if media_id in self.stop_ids:
                _unlink(dst)
                return
            if error:
                self._result(media_id, "failed", reasons, error)
                return
            # Alleen overschakelen als het record er nog is en nog het origineel gebruikt.
            sha, size = media_mod.sha256_file(dst), os.path.getsize(dst)
            cur = conn.execute(
                "UPDATE media SET playback_dir = 'renditions', playback_name = ?, playback_sha256 = ?, "
                "playback_size = ?, status = 'ok', warning = NULL WHERE id = ? AND playback_dir = 'files'",
                (os.path.basename(dst), sha, size, media_id))
            if cur.rowcount != 1:
                _unlink(dst)
                return
            secs = time.time() - started
            self._result(media_id, "optimized", reasons, row["warning"], encoder=self.current["encoder"], seconds=secs)
            log.info("Afspeelversie klaar voor '%s' (%s, %.0f s): %s", row["name"], self.current["encoder"], secs,
                     ", ".join(reasons))
        finally:
            conn.close()
            with self.lock:
                self.current = None
                self.stop_ids.discard(media_id)

    def _progress(self, p):
        with self.lock:
            if self.current:
                self.current["progress"] = p

    def _result(self, media_id, result, reasons, message, encoder=None, seconds=None):
        with self.lock:
            self.results[media_id] = {"result": result, "reasons": reasons, "message": message,
                                      "encoder": encoder, "seconds": round(seconds, 1) if seconds else None,
                                      "at": time.time()}
            self._save()

    def run_forever(self):
        time.sleep(5)   # de server eerst laten opstarten
        try:
            self.scan()
        except Exception as exc:  # noqa: BLE001
            log.warning("Video's controleren mislukt: %s", exc)
        while True:
            self.wake.wait(60)
            self.wake.clear()
            while True:
                with self.lock:
                    media_id = self.queue.pop(0) if self.queue else None
                    if media_id:   # meteen zichtbaar als 'bezig', ook tijdens het bekijken
                        self.current = {"id": media_id, "progress": 0.0, "encoder": None, "started": time.time()}
                if media_id is None:
                    break
                try:
                    if self.enabled():
                        self.process(media_id)
                except Exception as exc:  # noqa: BLE001
                    log.exception("Video omzetten mislukt: %s", exc)
                    self._result(media_id, "failed", [], str(exc))
                finally:
                    with self.lock:
                        self.current = None

    def start(self):
        threading.Thread(target=self.run_forever, daemon=True, name="signage-videoopt").start()

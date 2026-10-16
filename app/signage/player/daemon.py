"""Player-daemon.

Werking in het kort:
- Bij het starten wordt meteen de laatst bekende geldige playlist uit de cache gespeeld,
  zodat het scherm na een stroomstoring direct weer content toont, ook als de server
  (nog) niet bereikbaar is.
- Een achtergrondthread vraagt elke paar seconden de configuratie op bij de server
  (met ETag, dus vrijwel gratis), controleert alle bestanden en schrijft pas daarna
  de nieuwe configuratie atomair naar de cache. Een ongeldige of onvolledige
  configuratie vervangt nooit de laatst geldige.
- De hoofdlus stuurt mpv aan: afbeeldingen op een timer, video's tot het einde.
  Ontbrekende of kapotte bestanden worden overgeslagen. Als niets afspeelbaar is,
  verschijnt het standby-scherm.
- mpv wordt gecontroleerd; reageert het niet meer, dan wordt het herstart, met een
  afkoelperiode als dat te vaak gebeurt. De daemon meldt zich bij systemd's watchdog.
"""

import hashlib
import json
import logging
import os
import queue
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

from .. import __version__
from ..config import read_player_config
from .mpv import Mpv, MpvError

log = logging.getLogger("signage.player")

DEFAULT_PLAYER_CONFIG = "/etc/signage/player.ini"


# ---------------------------------------------------------------------------
# systemd notify (zonder externe afhankelijkheden)
# ---------------------------------------------------------------------------

def sd_notify(message):
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.connect(addr)
        s.sendall(message.encode())
        s.close()
    except OSError:
        pass


def atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def display_info():
    """Status van de HDMI-aansluiting(en) uit /sys/class/drm."""
    out = []
    base = "/sys/class/drm"
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return out
    for e in entries:
        if "HDMI" not in e and "Composite" not in e:
            continue
        d = os.path.join(base, e)
        try:
            with open(os.path.join(d, "status"), encoding="utf-8") as fh:
                status = fh.read().strip()
        except OSError:
            continue
        mode = None
        try:
            with open(os.path.join(d, "modes"), encoding="utf-8") as fh:
                mode = (fh.readline().strip() or None)
        except OSError:
            pass
        out.append({"connector": e.split("-", 1)[-1], "connected": status == "connected", "mode": mode})
    return out


# ---------------------------------------------------------------------------
# Synchronisatie met de server
# ---------------------------------------------------------------------------

class Syncer(threading.Thread):
    def __init__(self, pcfg, cache_dir, out_queue, status_fn):
        super().__init__(daemon=True, name="signage-sync")
        self.server = pcfg.get("server_url").rstrip("/")
        self.token = pcfg.get("token").strip()
        self.poll = max(2, int(pcfg.get("poll_interval")))
        self.hb_interval = max(5, int(pcfg.get("heartbeat_interval")))
        self.cache_dir = cache_dir
        self.media_cache = os.path.join(cache_dir, "media")
        os.makedirs(self.media_cache, exist_ok=True)
        self.out = out_queue
        self.status_fn = status_fn
        self.etag = None
        self.revision = None
        self.server_ok = None
        self.last_error = None
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _request(self, path, method="GET", data=None, headers=None, timeout=10):
        req = urllib.request.Request(self.server + path, method=method, data=data)
        req.add_header("Authorization", "Bearer " + self.token)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        return urllib.request.urlopen(req, timeout=timeout)

    def _download(self, url, dst, sha256, size):
        part = dst + ".part"
        h = hashlib.sha256()
        with self._request(url, timeout=30) as resp, open(part, "wb") as out:
            for block in iter(lambda: resp.read(1024 * 1024), b""):
                h.update(block)
                out.write(block)
            out.flush()
            os.fsync(out.fileno())
        if h.hexdigest() != sha256 or (size and os.path.getsize(part) != size):
            os.unlink(part)
            raise ValueError("controlegetal klopt niet")
        os.replace(part, dst)

    def resolve(self, entry):
        """Bepaal het lokale pad van een bestand; download indien nodig."""
        path = entry.get("path")
        size = entry.get("size")
        if path and os.path.isfile(path) and (not size or os.path.getsize(path) == size):
            return path
        cached = os.path.join(self.media_cache, entry["sha256"] + entry.get("ext", ""))
        if os.path.isfile(cached) and (not size or os.path.getsize(cached) == size):
            return cached
        self._download(entry["url"], cached, entry["sha256"], size)
        return cached

    def process_config(self, cfg):
        if not isinstance(cfg, dict) or not isinstance(cfg.get("items"), list):
            raise ValueError("ongeldige configuratie van server")
        errors = []
        for key in ("items", "fallback_items"):
            resolved = []
            for it in cfg.get(key) or []:
                try:
                    it["local"] = self.resolve(it)
                    resolved.append(it)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Bestand '{it.get('name')}' niet beschikbaar: {exc}")
            cfg[key] = resolved
        sb = cfg.get("standby") or {}
        try:
            sb["local"] = self.resolve({**sb, "ext": ".png", "size": None}) if sb.get("sha256") else None
        except Exception as exc:  # noqa: BLE001
            sb["local"] = None
            errors.append(f"Standby-scherm niet beschikbaar: {exc}")
        cfg["standby"] = sb
        return cfg, errors

    def cleanup_cache(self, cfg):
        keep = set()
        for key in ("items", "fallback_items"):
            for it in cfg.get(key) or []:
                keep.add(os.path.basename(it.get("local") or ""))
        keep.add(os.path.basename((cfg.get("standby") or {}).get("local") or ""))
        for f in os.listdir(self.media_cache):
            if f not in keep:
                try:
                    os.unlink(os.path.join(self.media_cache, f))
                except OSError:
                    pass

    def fetch_once(self):
        headers = {"If-None-Match": self.etag} if self.etag else {}
        try:
            with self._request("/api/player/v1/config", headers=headers) as resp:
                cfg = json.loads(resp.read().decode("utf-8"))
                etag = resp.headers.get("ETag")
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                self.server_ok = True
                return
            self.server_ok = False
            self.last_error = f"Server antwoordde {exc.code}"
            if exc.code == 401:
                self.last_error = "Player-token wordt geweigerd door de server."
            return
        except (urllib.error.URLError, OSError, ValueError) as exc:
            if self.server_ok is not False:
                log.warning("Server niet bereikbaar (%s); laatst bekende playlist blijft actief.", exc)
            self.server_ok = False
            self.last_error = "Server niet bereikbaar."
            return
        self.server_ok = True
        self.last_error = None
        if cfg.get("revision") == self.revision:
            self.etag = etag
            return
        cfg, errors = self.process_config(cfg)
        for e in errors:
            log.warning(e)
        atomic_write_json(os.path.join(self.cache_dir, "last-config.json"), cfg)
        self.cleanup_cache(cfg)
        self.revision = cfg.get("revision")
        # ETag alleen onthouden als alles beschikbaar is; anders volgende keer opnieuw proberen.
        self.etag = etag if not errors else None
        self.out.put((cfg, errors))

    def heartbeat(self):
        status = self.status_fn()
        data = json.dumps(status).encode()
        try:
            with self._request("/api/player/v1/heartbeat", method="POST", data=data,
                               headers={"Content-Type": "application/json"}, timeout=5):
                pass
        except (urllib.error.URLError, OSError):
            pass

    def run(self):
        last_hb = 0
        while not self._stop.is_set():
            try:
                self.fetch_once()
            except Exception as exc:  # noqa: BLE001
                log.exception("Synchronisatie mislukt: %s", exc)
            if time.time() - last_hb >= self.hb_interval:
                self.heartbeat()
                last_hb = time.time()
            self._stop.wait(self.poll)


# ---------------------------------------------------------------------------
# Afspelen
# ---------------------------------------------------------------------------

class Player:
    PING_INTERVAL = 2.0
    FAIL_IDLE_AFTER = 3.0        # mpv idle zo lang na loadfile => bestand mislukt
    STANDBY_RETRY = 30.0
    MAX_RESTARTS = 5
    RESTART_WINDOW = 600.0
    COOLDOWN = 60.0

    def __init__(self, pcfg):
        self.pcfg = pcfg
        self.cache_dir = pcfg.get("cache_dir")
        os.makedirs(self.cache_dir, exist_ok=True)
        runtime = os.environ.get("RUNTIME_DIRECTORY") or self.cache_dir
        self.mpv = Mpv(pcfg.get("mpv_binary"), os.path.join(runtime, "mpv.sock"),
                       hwdec=pcfg.get("hwdec"), vo=pcfg.get("vo"), gpu_context=pcfg.get("gpu_context"),
                       audio_device=pcfg.get("audio_device"), extra_args=pcfg.get("extra_args"))
        self.queue = queue.Queue()
        self.cfg = None
        self.items = []
        self.mode = "starting"      # playing | hold | fallback | standby | error | starting
        self.index = -1
        self.loaded_at = 0.0
        self.deadline = None
        self.current = None
        self.consecutive_failures = 0
        self.standby_until = 0.0
        self.restarts = []
        self.total_restarts = 0
        self.cooldown_until = 0.0
        self.next_start_at = 0.0
        self.errors = []
        self.message = None
        self.started = time.time()
        self.last_ping = 0.0
        self.lock = threading.Lock()
        self.running = True
        self.applied_volume = None
        self.applied_rotation = None
        self.applied_mute = None
        self.mpv_started_once = False
        self.standby_reason = None
        self.syncer = Syncer(pcfg, self.cache_dir, self.queue, self.status)

    # -- status ------------------------------------------------------------------
    def error(self, text):
        log.warning(text)
        with self.lock:
            self.errors.append(time.strftime("%H:%M:%S ") + text)
            self.errors = self.errors[-10:]

    def status(self):
        with self.lock:
            cur = self.current
            playlist = (self.cfg or {}).get("playlist") or {}
            return {
                "state": "error" if self.mode == "error" else ("standby" if self.mode == "standby" else
                                                               ("playing" if cur else "starting")),
                "current": ({"media_id": cur.get("media_id"), "name": cur.get("name"), "kind": cur.get("kind"),
                             "index": self.index, "total": len(self.items)} if cur and self.mode != "standby" else None),
                "playlist_id": playlist.get("id"),
                "revision": (self.cfg or {}).get("revision"),
                "mpv_restarts": self.total_restarts,
                "errors": list(self.errors),
                "display": display_info(),
                "version": __version__,
                "uptime": time.time() - self.started,
                "message": self.message or self.syncer.last_error,
            }

    # -- configuratie --------------------------------------------------------------
    def load_cache(self):
        path = os.path.join(self.cache_dir, "last-config.json")
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
            if isinstance(cfg, dict) and isinstance(cfg.get("items"), list):
                log.info("Laatst bekende playlist uit cache geladen (revisie %s).", cfg.get("revision"))
                self.syncer.revision = cfg.get("revision")
                return cfg
        except (OSError, ValueError):
            pass
        return None

    def apply_config(self, cfg):
        self.cfg = cfg
        self.syncer.revision = cfg.get("revision")
        playlist = cfg.get("playlist") or {}
        log.info("Configuratie actief: playlist %s, %d item(s).", playlist.get("name") or "-", len(cfg["items"]))
        self.items = list(cfg.get("items") or [])
        self.mode = "playing"
        self.index = -1
        self.consecutive_failures = 0
        self.standby_until = 0.0
        self.apply_properties()
        self.next_item()

    def apply_properties(self):
        if not self.cfg or not self.mpv.alive():
            return
        vol = int(self.cfg.get("volume", 80))
        rot = int((self.cfg.get("player") or {}).get("rotation", 0))
        mute = bool((self.cfg.get("playlist") or {}).get("mute", False))
        try:
            if vol != self.applied_volume:
                self.mpv.set("volume", vol)
                self.applied_volume = vol
            if rot != self.applied_rotation:
                self.mpv.set("video-rotate", rot)
                self.applied_rotation = rot
            if mute != self.applied_mute:
                self.mpv.set("mute", mute)
                self.applied_mute = mute
        except MpvError as exc:
            self.error(f"Instelling toepassen mislukt: {exc}")

    # -- mpv-beheer ----------------------------------------------------------------
    def ensure_mpv(self):
        if self.mpv.alive():
            return True
        now = time.time()
        if now < self.cooldown_until or now < self.next_start_at:
            return False
        self.restarts = [t for t in self.restarts if now - t < self.RESTART_WINDOW]
        if len(self.restarts) >= self.MAX_RESTARTS:
            self.cooldown_until = now + self.COOLDOWN
            self.restarts = []
            self.mode = "error"
            self.message = "mpv crasht herhaaldelijk; nieuwe poging over 60 s."
            self.error(self.message)
            return False
        is_restart = self.mpv_started_once
        if is_restart:
            log.warning("mpv opnieuw starten.")
            self.total_restarts += 1
        self.restarts.append(now)
        try:
            rot = int(((self.cfg or {}).get("player") or {}).get("rotation", 0))
            self.mpv.initial_rotation = rot
            self.mpv.initial_volume = int((self.cfg or {}).get("volume", 80))
            self.mpv.start()
        except MpvError as exc:
            self.mode = "error"
            self.message = str(exc)
            self.error(str(exc))
            self.next_start_at = time.time() + 5
            return False
        self.mpv_started_once = True
        self.message = None
        self.applied_volume = self.applied_rotation = self.applied_mute = None
        self.apply_properties()
        if self.mode == "error":
            self.mode = "playing"
        if self.mode == "standby":
            self.show_standby(self.standby_reason or "")
        elif is_restart and self.current is not None:
            # Na een crash door naar het volgende item, zodat één kapot bestand
            # geen herstartlus veroorzaakt.
            self.next_item()
        return True

    def restart_mpv(self, reason):
        self.error(f"mpv wordt herstart: {reason}")
        self.mpv.stop()

    # -- afspelen ------------------------------------------------------------------
    def show_standby(self, reason):
        self.mode = "standby"
        self.standby_reason = reason
        self.current = None
        self.deadline = None
        self.standby_until = time.time() + self.STANDBY_RETRY
        sb = ((self.cfg or {}).get("standby") or {}).get("local")
        if sb and os.path.isfile(sb) and self.mpv.alive():
            try:
                self.mpv.command("loadfile", sb, "replace")
                self.loaded_at = time.time()
            except MpvError as exc:
                self.error(f"Standby-scherm tonen mislukt: {exc}")
        log.info("Standby-scherm: %s", reason)

    def load(self, item):
        self.current = item
        path = item.get("local")
        if not path or not os.path.isfile(path):
            self.fail(f"Bestand ontbreekt: {item.get('name')}")
            return
        try:
            self.mpv.command("loadfile", path, "replace")
            # Met --keep-open pauzeert mpv aan het einde van een video, en die pauze
            # blijft gelden voor het volgende bestand. Daarom altijd expliciet hervatten.
            self.mpv.set("pause", False)
        except MpvError as exc:
            self.restart_mpv(str(exc))
            return
        self.loaded_at = time.time()
        if item.get("kind") == "image":
            self.deadline = self.loaded_at + float(item.get("duration") or 10)
        else:
            self.deadline = None

    def fail(self, text):
        self.error(text)
        self.consecutive_failures += 1
        if self.consecutive_failures >= max(1, len(self.items)):
            self.show_standby("geen afspeelbare bestanden")
        else:
            self.next_item()

    def next_item(self):
        if not self.items:
            self.show_standby("lege of geen playlist")
            return
        nxt = self.index + 1
        if nxt >= len(self.items):
            mode = ((self.cfg or {}).get("playlist") or {}).get("repeat_mode", "loop")
            if mode == "once_hold_last" and self.mode != "fallback":
                if self.mode != "hold":
                    log.info("Playlist eenmaal afgespeeld; laatste item blijft staan.")
                self.mode = "hold"
                self.deadline = None
                return
            if mode == "once_then_default" and self.mode != "fallback" and (self.cfg or {}).get("fallback_items"):
                log.info("Playlist eenmaal afgespeeld; overschakelen naar standaardplaylist.")
                self.items = list(self.cfg["fallback_items"])
                self.mode = "fallback"
                self.consecutive_failures = 0
            nxt = 0
        self.index = nxt
        self.load(self.items[self.index])

    def tick(self):
        now = time.time()
        # Nieuwe configuratie?
        try:
            cfg, errors = self.queue.get_nowait()
            for e in errors:
                self.error(e)
            if self.mpv.alive():
                self.apply_config(cfg)
            else:
                log.info("Configuratie klaargezet (%d item(s)); start zodra mpv draait.", len(cfg.get("items") or []))
                self.cfg = cfg
                self.items = list(cfg.get("items") or [])
                self.index = -1
                self.mode = "playing"
                self.current = None
        except queue.Empty:
            pass

        if not self.ensure_mpv():
            return
        if self.cfg is not None and self.current is None and self.mode not in ("standby", "hold"):
            self.next_item()
            return

        try:
            events = self.mpv.drain_events(0)
        except MpvError as exc:
            self.restart_mpv(str(exc))
            return
        for ev in events:
            if ev.get("event") == "end-file" and ev.get("reason") == "error":
                detail = ev.get("file_error", "onbekende fout")
                if self.mode != "standby" and self.current is not None:
                    # Het vorige bestand eindigt bij 'loadfile replace' met reason=stop;
                    # een error hoort dus bij het zojuist geladen item.
                    self.fail(f"Kan '{self.current.get('name')}' niet afspelen ({detail}).")
                    return
                log.warning("mpv kon bestand niet afspelen: %s", detail)

        if self.mode == "standby":
            if now >= self.standby_until and self.items:
                self.consecutive_failures = 0
                self.mode = "playing"
                self.index = -1
                self.next_item()
            return

        if self.current is None:
            return

        if now - self.last_ping >= (0.5 if self.current.get("kind") == "video" else self.PING_INTERVAL):
            self.last_ping = now
            try:
                idle = self.mpv.get("idle-active", False)
                eof = self.mpv.get("eof-reached", False) if self.current.get("kind") == "video" else False
            except MpvError as exc:
                self.restart_mpv(f"reageert niet ({exc})")
                return
            since = now - self.loaded_at
            if idle and since > self.FAIL_IDLE_AFTER:
                self.fail(f"Kan '{self.current.get('name')}' niet afspelen (beschadigd of niet ondersteund).")
                return
            if eof and since > 1.0 and self.mode != "hold":
                self.consecutive_failures = 0
                self.next_item()
                return
            vd = self.current.get("video_duration")
            if self.current.get("kind") == "video" and vd and since > vd * 1.5 + 30 and self.mode != "hold":
                self.restart_mpv(f"video '{self.current.get('name')}' loopt vast")
                self.next_item()
                return
            if not idle and since > self.FAIL_IDLE_AFTER:
                self.consecutive_failures = 0

        if self.deadline is not None and now >= self.deadline and self.mode != "hold":
            self.consecutive_failures = 0   # dit item is volledig getoond
            self.next_item()

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))
        cached = self.load_cache()
        if cached:
            self.queue.put((cached, []))
        self.syncer.start()
        sd_notify("READY=1")
        log.info("Signage-player %s gestart.", __version__)
        while self.running:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("Onverwachte fout in de player: %s", exc)
                self.error(f"Interne fout: {exc}")
                time.sleep(1)
            sd_notify("WATCHDOG=1")
            time.sleep(0.2)
        sd_notify("STOPPING=1")
        self.syncer.stop()
        self.mpv.stop()


def main():
    logging.basicConfig(level=os.environ.get("SIGNAGE_LOG_LEVEL", "INFO"),
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    path = os.environ.get("SIGNAGE_PLAYER_CONFIG", DEFAULT_PLAYER_CONFIG)
    pcfg = read_player_config(path)
    if not pcfg.get("token"):
        log.error("Geen player-token in %s; de player kan zich niet aanmelden.", path)
    Player(pcfg).run()


if __name__ == "__main__":
    main()

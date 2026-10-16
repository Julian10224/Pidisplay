"""Synchronisatie met de server en gedeelde hulpfuncties van de player.

- Vraagt elke paar seconden de configuratie op (ETag, dus vrijwel gratis).
- Controleert alle bestanden en downloadt ze indien nodig (externe player), met
  SHA-256-controle. HTML-projecten worden veilig uitgepakt (htmlpkg).
- Schrijft pas daarna de configuratie atomair naar de cache; een onvolledige
  configuratie vervangt nooit de laatst geldige.
- Haalt in een eigen thread opdrachten voor de live bediening op (long polling) en geeft
  die door aan de weergave. Na een wissel van item meldt de player zich direct (kick),
  zodat de website binnen een seconde laat zien wat er op het scherm staat.
"""

import hashlib
import json
import logging
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import htmlpkg

log = logging.getLogger("signage.player")

CONTROL_WAIT = 25      # zelfde als de server
CONTROL_TTL = 15       # oudere opdrachten worden niet meer uitgevoerd
KICK_MIN_GAP = 0.3     # minimaal zoveel seconden tussen twee extra statusmeldingen


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


def mem_available():
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def load_cached_config(cache_dir):
    path = os.path.join(cache_dir, "last-config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        if isinstance(cfg, dict) and isinstance(cfg.get("items"), list):
            return cfg
    except (OSError, ValueError):
        pass
    return None


class Syncer(threading.Thread):
    def __init__(self, pcfg, cache_dir, out_queue, status_fn, on_command=None):
        super().__init__(daemon=True, name="signage-sync")
        self.server = pcfg.get("server_url").rstrip("/")
        self.token = pcfg.get("token").strip()
        self.poll = max(2, int(pcfg.get("poll_interval")))
        self.hb_interval = max(5, int(pcfg.get("heartbeat_interval")))
        self.cache_dir = cache_dir
        self.media_cache = os.path.join(cache_dir, "media")
        self.html_cache = os.path.join(cache_dir, "html")
        os.makedirs(self.media_cache, exist_ok=True)
        os.makedirs(self.html_cache, exist_ok=True)
        self.out = out_queue
        self.status_fn = status_fn
        self.etag = None
        self.revision = None
        self.server_ok = None
        self.last_error = None
        self._stop = threading.Event()
        self._kick = threading.Event()
        self.on_command = on_command
        self.control_supported = None

    def stop(self):
        self._stop.set()
        self._kick.set()

    def kick(self):
        """Status zo snel mogelijk melden (bijv. na een wissel van item)."""
        self._kick.set()

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
        """Lokaal pad van een bestand; download indien nodig."""
        path = entry.get("path")
        size = entry.get("size")
        if path and os.path.isfile(path) and (not size or os.path.getsize(path) == size):
            return path
        cached = os.path.join(self.media_cache, entry["sha256"] + entry.get("ext", ""))
        if os.path.isfile(cached) and (not size or os.path.getsize(cached) == size):
            return cached
        self._download(entry["url"], cached, entry["sha256"], size)
        return cached

    def resolve_html(self, html):
        """Lokale projectmap van een geüpload HTML-project; download en pak uit indien nodig."""
        entry = html.get("entry") or "index.html"
        path = html.get("path")
        if path and os.path.isfile(os.path.join(path, entry)):
            return path
        dest = os.path.join(self.html_cache, html["sha256"])
        if os.path.isfile(os.path.join(dest, entry)):
            return dest
        archive = self.resolve({"url": html["url"], "sha256": html["sha256"], "size": html.get("size"),
                                "ext": html.get("ext", ".zip"), "path": None})
        htmlpkg.extract_project(archive, "project" + html.get("ext", ".zip"), dest)
        return dest

    def process_config(self, cfg):
        if not isinstance(cfg, dict) or not isinstance(cfg.get("items"), list):
            raise ValueError("ongeldige configuratie van server")
        errors = []
        for key in ("items", "fallback_items"):
            resolved = []
            for it in cfg.get(key) or []:
                try:
                    if it.get("kind") == "html":
                        h = it.get("html") or {}
                        if h.get("source") == "upload":
                            h["local"] = self.resolve_html(h)
                        elif h.get("page") not in htmlpkg.BUILTIN_PAGES:
                            raise ValueError("onbekende ingebouwde pagina")
                        it["local"] = h.get("local")
                    else:
                        it["local"] = self.resolve(it)
                    lg = it.get("logo")
                    if lg:
                        try:
                            lg["local"] = self.resolve(lg)
                        except Exception as exc:  # noqa: BLE001
                            it["logo"] = None   # item wordt zonder logo getoond
                            errors.append(f"Logo bij '{it.get('name')}' niet beschikbaar: {exc}")
                    resolved.append(it)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Bestand '{it.get('name')}' niet beschikbaar: {exc}")
            cfg[key] = resolved
        transitions = {}
        for tid, t in (cfg.get("transitions") or {}).items():
            try:
                t["local"] = self.resolve(t)
                transitions[tid] = t
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Overgang '{t.get('name')}' niet beschikbaar: {exc}")
        cfg["transitions"] = transitions
        for key in ("items", "fallback_items"):
            for it in cfg[key]:
                if it.get("transition") and it["transition"] not in transitions:
                    it["transition"] = None   # overgang ontbreekt: overslaan
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
                keep.add(os.path.basename((it.get("logo") or {}).get("local") or ""))
                h = it.get("html") or {}
                if h.get("sha256"):
                    keep.add(h["sha256"] + h.get("ext", ""))
        for t in (cfg.get("transitions") or {}).values():
            keep.add(os.path.basename(t.get("local") or ""))
        keep.add(os.path.basename((cfg.get("standby") or {}).get("local") or ""))
        for d in (self.media_cache, self.html_cache):
            for f in os.listdir(d):
                if f not in keep:
                    p = os.path.join(d, f)
                    try:
                        if os.path.isdir(p):
                            import shutil
                            shutil.rmtree(p, ignore_errors=True)
                        else:
                            os.unlink(p)
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

    # -- live bediening ---------------------------------------------------------
    def control_once(self, epoch, seq, wait=CONTROL_WAIT):
        """Eén ophaalronde. Geeft (epoch, seq, pauze) terug."""
        q = {"epoch": epoch or "", "wait": str(wait if seq is not None else 0)}
        if seq is not None:
            q["after"] = str(seq)
        try:
            with self._request("/api/player/v1/control?" + urllib.parse.urlencode(q),
                               timeout=CONTROL_WAIT + 10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:      # oudere server zonder live bediening
                self.control_supported = False
                return epoch, seq, 60
            return epoch, seq, 5
        except (urllib.error.URLError, OSError, ValueError):
            return epoch, seq, 5
        self.control_supported = True
        new_epoch, new_seq = data.get("epoch"), int(data.get("seq") or 0)
        if new_epoch != epoch or seq is None:
            return new_epoch, new_seq, 0     # eerste contact of server herstart: niets uitvoeren
        for c in data.get("commands") or []:
            try:
                if int(c.get("seq")) > seq and float(c.get("age") or 0) < CONTROL_TTL:
                    self.on_command(c)
            except Exception as exc:  # noqa: BLE001
                log.warning("Opdracht kon niet worden uitgevoerd: %s", exc)
        return epoch, new_seq, float(data.get("retry") or 0)

    def control_loop(self):
        epoch, seq = None, None
        while not self._stop.is_set():
            epoch, seq, pause = self.control_once(epoch, seq)
            if pause:
                self._stop.wait(pause)

    def run(self):
        if self.on_command is not None:
            threading.Thread(target=self.control_loop, daemon=True, name="signage-control").start()
        last_hb = last_fetch = last_kick = 0.0
        while not self._stop.is_set():
            now = time.time()
            if now - last_fetch >= self.poll:
                last_fetch = now
                try:
                    self.fetch_once()
                except Exception as exc:  # noqa: BLE001
                    log.exception("Synchronisatie mislukt: %s", exc)
            kicked = self._kick.is_set()
            if kicked and time.time() - last_kick < KICK_MIN_GAP:
                self._stop.wait(KICK_MIN_GAP)
            if kicked or time.time() - last_hb >= self.hb_interval:
                self._kick.clear()
                self.heartbeat()
                last_hb = last_kick = time.time()
            now = time.time()
            self._kick.wait(max(0.05, min(last_fetch + self.poll, last_hb + self.hb_interval) - now))

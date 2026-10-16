"""Browserweergave: Chromium in kioskmodus toont één lokale playerpagina die foto's,
video's, HTML-pagina's en overgangen afspeelt met voorgeladen lagen.

De daemon:
- levert via een lokale webserver (alleen 127.0.0.1) de playerpagina, de gecachte media
  en HTML-projecten. De player werkt daardoor ook als de signage-server even weg is;
- start Chromium en houdt het in de gaten (proces, levenstekens van de pagina, geheugen);
- stuurt de status van de pagina door naar de server.

HTML-projecten worden geleverd vanaf http://localhost:<poort> terwijl de playerpagina op
http://127.0.0.1:<poort> draait. Dat zijn verschillende origins: een HTML-pagina kan de
player dus niet beïnvloeden of blokkeren, en draait in een eigen renderproces.
"""

import json
import logging
import mimetypes
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlsplit

from .. import __version__, htmlpkg
from .sync import Syncer, display_info, load_cached_config, mem_available, sd_notify

log = logging.getLogger("signage.player")

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
PLAYER_DIR = os.path.join(STATIC, "player")
DISPLAY_DIR = os.path.join(STATIC, "display")

MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
        ".webm": "video/webm", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
        ".html": "text/html", ".htm": "text/html", ".svg": "image/svg+xml", ".json": "application/json",
        ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf"}

CHROMIUM_FLAGS = [
    "--kiosk", "--no-first-run", "--no-default-browser-check", "--noerrdialogs", "--disable-infobars",
    "--disable-session-crashed-bubble", "--disable-features=Translate,MediaRouter,OptimizationHints",
    "--autoplay-policy=no-user-gesture-required", "--check-for-update-interval=31536000",
    "--disable-component-update", "--disable-background-networking", "--disable-sync",
    "--disable-default-apps", "--disable-breakpad", "--password-store=basic", "--disable-pinch",
    "--overscroll-history-navigation=0", "--force-device-scale-factor=1", "--hide-scrollbars",
    "--disk-cache-size=67108864", "--disable-extensions",
    # Elke site in een eigen renderproces: een vastgelopen HTML-pagina (localhost) kan de
    # playerpagina (127.0.0.1) dan niet bevriezen.
    "--site-per-process",
]


def find_chromium(setting):
    if setting and setting != "auto":
        return setting if shutil.which(setting) or os.path.exists(setting) else None
    for name in ("chromium", "chromium-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


class PageState:
    """Vertaalt de gecachte configuratie naar wat de playerpagina nodig heeft."""

    def __init__(self, port):
        self.port = port
        self.files = {}      # naam -> lokaal pad
        self.html_dirs = {}  # sleutel -> projectmap
        self.data = None
        self.lock = threading.Lock()

    def _file(self, path):
        if not path:
            return None
        name = os.path.basename(path)
        self.files[name] = path
        return "/m/" + quote(name)

    def _item(self, it, idx, system_name):
        out = {k: it.get(k) for k in ("media_id", "name", "kind", "scale", "duration", "video_duration",
                                      "transition")}
        out["key"] = f"{idx}:{it.get('media_id')}"
        if it.get("kind") == "html":
            h = it.get("html") or {}
            if h.get("source") == "builtin":
                spec = htmlpkg.BUILTIN_PAGES.get(h.get("page"))
                if not spec:
                    return None
                src = f"http://localhost:{self.port}/d/{spec['file']}?name={quote(system_name or '')}"
            else:
                local = h.get("local")
                if not local:
                    return None
                key = os.path.basename(local)
                self.html_dirs[key] = local
                src = f"http://localhost:{self.port}/h/{quote(key)}/{quote(h.get('entry') or 'index.html')}"
            out["html"] = {"src": src, "width": h.get("width"), "height": h.get("height")}
            out["src"] = src
        else:
            out["src"] = self._file(it.get("local"))
            if not out["src"]:
                return None
        return out

    def update(self, cfg):
        with self.lock:
            self.files, self.html_dirs = {}, {}
            name = cfg.get("system_name")
            items = [x for x in (self._item(it, i, name) for i, it in enumerate(cfg.get("items") or [])) if x]
            fb = [x for x in (self._item(it, i, name) for i, it in enumerate(cfg.get("fallback_items") or [])) if x]
            transitions = {}
            for tid, t in (cfg.get("transitions") or {}).items():
                src = self._file(t.get("local"))
                if src:
                    transitions[tid] = {"src": src, "name": t.get("name"), "video_duration": t.get("video_duration")}
            standby = self._file((cfg.get("standby") or {}).get("local"))
            self.data = {
                "revision": cfg.get("revision"), "playlist": cfg.get("playlist"), "items": items,
                "fallback_items": fb, "transitions": transitions, "scale_default": cfg.get("scale_default", "cover"),
                "volume": cfg.get("volume", 80), "rotation": (cfg.get("player") or {}).get("rotation", 0),
                "standby_src": standby, "system_name": name,
            }

    def json(self):
        with self.lock:
            return json.dumps(self.data or {"revision": None, "items": []})


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, page_state, on_status):
        self.page_state = page_state
        self.on_status = on_status
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "signage-player"

    def log_message(self, fmt, *args):  # geen logregel per verzoek (SD-kaart sparen)
        pass

    def _send_bytes(self, code, data, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_file(self, path, cache=True):
        if not path or not os.path.isfile(path):
            return self._send_bytes(404, b"niet gevonden", "text/plain")
        size = os.path.getsize(path)
        ext = os.path.splitext(path)[1].lower()
        ctype = MIME.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        code = 200
        if rng and rng.startswith("bytes="):
            try:
                a, b = rng[6:].split(",")[0].split("-")
                if a == "":
                    start = max(0, size - int(b))
                else:
                    start = int(a)
                    end = min(size - 1, int(b)) if b else size - 1
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                code = 206
            except ValueError:
                start, end = 0, size - 1
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    block = fh.read(min(256 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        self.do_GET()

    def _host(self):
        return (self.headers.get("Host") or "").split(":")[0].lower()

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        ps = self.server.page_state
        # Twee origins op één poort: de playerpagina en de media op 127.0.0.1, HTML-content op
        # localhost. HTML-content krijgt zo niets van de player te zien (status, configuratie)
        # en kan die ook niet beïnvloeden; de player levert omgekeerd geen HTML-projecten.
        host = self._host()
        if host == "localhost":
            if not (path.startswith("/h/") or path.startswith("/d/")):
                return self._send_bytes(404, b"niet gevonden", "text/plain")
        elif path.startswith("/h/") or path.startswith("/d/"):
            return self._send_bytes(404, b"niet gevonden", "text/plain")
        if path in ("/", "/index.html"):
            return self._send_file(os.path.join(PLAYER_DIR, "player.html"), cache=False)
        if path in ("/player.js", "/player.css"):
            return self._send_file(os.path.join(PLAYER_DIR, path[1:]), cache=False)
        if path == "/state":
            return self._send_bytes(200, ps.json().encode(), "application/json")
        if path.startswith("/m/"):
            with ps.lock:
                p = ps.files.get(path[3:])
            return self._send_file(p)
        if path.startswith("/h/"):
            parts = path[3:].split("/", 1)
            if len(parts) == 2:
                with ps.lock:
                    root = ps.html_dirs.get(parts[0])
                if root:
                    return self._send_file(htmlpkg.safe_join(root, parts[1]), cache=False)
            return self._send_bytes(404, b"niet gevonden", "text/plain")
        if path.startswith("/d/"):
            fname = path[3:]
            allowed = {s["file"] for s in htmlpkg.BUILTIN_PAGES.values()} | {"display.css"}
            if fname in allowed:
                return self._send_file(os.path.join(DISPLAY_DIR, fname))
        return self._send_bytes(404, b"niet gevonden", "text/plain")

    def do_POST(self):
        if urlsplit(self.path).path != "/status" or self._host() != "127.0.0.1":
            return self._send_bytes(404, b"", "text/plain")
        try:
            n = min(int(self.headers.get("Content-Length") or 0), 65536)
            data = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send_bytes(400, b"", "text/plain")
        if isinstance(data, dict):
            self.server.on_status(data)
        return self._send_bytes(204, b"", "text/plain")


class BrowserRenderer:
    MAX_RESTARTS = 5
    RESTART_WINDOW = 600.0
    COOLDOWN = 60.0
    PAGE_STALE = 45.0
    STARTUP_GRACE = 90.0
    LOW_MEMORY = 60 * 1024 * 1024

    def __init__(self, pcfg, launch_browser=True):
        self.pcfg = pcfg
        self.cache_dir = pcfg.get("cache_dir")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.port = int(pcfg.get("local_port", "8765") or 8765)
        self.queue = queue.Queue()
        self.page_state = PageState(self.port)
        self.page_status = {}
        self.page_seen = 0.0
        self.cfg = None
        self.errors = []
        self.message = None
        self.started = time.time()
        self.lock = threading.Lock()
        self.running = True
        self.launch_browser = launch_browser
        self.proc = None
        self.proc_started = 0.0
        self.restarts = []
        self.total_restarts = 0
        self.cooldown_until = 0.0
        self.next_start_at = 0.0
        self.low_mem_since = None
        self.server = None
        self.syncer = Syncer(pcfg, self.cache_dir, self.queue, self.status)

    # -- status ------------------------------------------------------------------
    def error(self, text):
        log.warning(text)
        with self.lock:
            self.errors.append(time.strftime("%H:%M:%S ") + text)
            self.errors = self.errors[-10:]

    def on_page_status(self, data):
        with self.lock:
            self.page_status = data
            self.page_seen = time.time()

    def status(self):
        with self.lock:
            ps = dict(self.page_status)
            stale = time.time() - self.page_seen > self.PAGE_STALE
            errors = (list(self.errors) + [str(e) for e in (ps.get("errors") or [])][-10:])[-10:]
            phase = ps.get("phase") if not stale else None
            playlist = (self.cfg or {}).get("playlist") or {}
            state = "starting"
            if self.message and stale:
                state = "error"
            elif phase == "STANDBY":
                state = "standby"
            elif phase:
                state = "playing" if ps.get("current") else "starting"
            return {
                "state": state, "phase": phase, "renderer": "browser",
                "current": ps.get("current") if not stale else None,
                "playlist_id": playlist.get("id"), "revision": (self.cfg or {}).get("revision"),
                "mpv_restarts": self.total_restarts, "errors": errors, "display": display_info(),
                "viewport": ps.get("viewport"), "video_stats": ps.get("video_stats"),
                "version": __version__, "uptime": time.time() - self.started,
                "message": self.message or self.syncer.last_error,
            }

    # -- Chromium ----------------------------------------------------------------
    def _mark_clean_exit(self, profile):
        """Voorkom de melding 'Chromium is niet correct afgesloten' na een stroomstoring."""
        pref = os.path.join(profile, "Default", "Preferences")
        try:
            with open(pref, encoding="utf-8") as fh:
                data = json.load(fh)
            data.setdefault("profile", {})["exit_type"] = "Normal"
            data["profile"]["exited_cleanly"] = True
            with open(pref + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(pref + ".tmp", pref)
        except (OSError, ValueError):
            pass

    def chromium_command(self, binary):
        profile = os.path.join(self.cache_dir, "chromium")
        os.makedirs(profile, exist_ok=True)
        self._mark_clean_exit(profile)
        cmd = [binary, *CHROMIUM_FLAGS, f"--user-data-dir={profile}"]
        if os.environ.get("WAYLAND_DISPLAY"):
            cmd.append("--ozone-platform=wayland")
        extra = (self.pcfg.get("chromium_extra_args") or "").split()
        cmd += extra
        cmd.append(f"http://127.0.0.1:{self.port}/")
        return cmd

    def ensure_browser(self):
        if not self.launch_browser:
            return
        if self.proc is not None and self.proc.poll() is None:
            return
        now = time.time()
        if self.proc is not None:
            self.error(f"Chromium is gestopt (exitcode {self.proc.returncode}).")
            self.proc = None
        if now < self.cooldown_until or now < self.next_start_at:
            return
        self.restarts = [t for t in self.restarts if now - t < self.RESTART_WINDOW]
        if len(self.restarts) >= self.MAX_RESTARTS:
            self.cooldown_until = now + self.COOLDOWN
            self.restarts = []
            self.message = "Chromium start herhaaldelijk niet; nieuwe poging over 60 s."
            self.error(self.message)
            return
        binary = find_chromium(self.pcfg.get("chromium_binary", "auto"))
        if not binary:
            self.message = "Chromium is niet geïnstalleerd."
            self.next_start_at = now + 60
            self.error(self.message)
            return
        if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
            self.message = "Geen grafische omgeving (cage) gevonden voor Chromium."
            self.next_start_at = now + 60
            self.error(self.message)
            return
        if self.restarts:
            self.total_restarts += 1
        self.restarts.append(now)
        cmd = self.chromium_command(binary)
        log.info("Chromium starten: %s", " ".join(cmd))
        env = dict(os.environ)
        env.setdefault("HOME", self.cache_dir)
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, env=env, start_new_session=True)
            self.proc_started = now
            self.message = None
        except OSError as exc:
            self.message = f"Chromium kon niet starten: {exc}"
            self.error(self.message)
            self.next_start_at = now + 10

    def stop_browser(self, reason=None):
        if reason:
            self.error(f"Chromium wordt herstart: {reason}")
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except OSError:
                    pass
        self.proc = None

    def watchdog(self):
        if not self.launch_browser or self.proc is None or self.proc.poll() is not None:
            return
        now = time.time()
        if now - self.proc_started > self.STARTUP_GRACE and now - max(self.page_seen, self.proc_started) > self.PAGE_STALE:
            self.stop_browser("de playerpagina reageert niet meer")
            return
        avail = mem_available()
        if avail is not None and avail < self.LOW_MEMORY:
            self.low_mem_since = self.low_mem_since or now
            if now - self.low_mem_since > 60:
                self.low_mem_since = None
                self.stop_browser("te weinig vrij geheugen")
        else:
            self.low_mem_since = None

    # -- hoofdlus ----------------------------------------------------------------
    def apply(self, cfg, errors=()):
        for e in errors:
            self.error(e)
        self.cfg = cfg
        self.syncer.revision = cfg.get("revision")
        self.page_state.update(cfg)
        log.info("Configuratie klaargezet voor de playerpagina: playlist %s, %d item(s).",
                 (cfg.get("playlist") or {}).get("name") or "-", len(cfg.get("items") or []))

    def start_server(self):
        self.server = LocalServer(self.port, self.page_state, self.on_page_status)
        threading.Thread(target=self.server.serve_forever, daemon=True, name="signage-local-http").start()

    def tick(self):
        try:
            cfg, errors = self.queue.get_nowait()
            self.apply(cfg, errors)
        except queue.Empty:
            pass
        self.ensure_browser()
        self.watchdog()

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))
        cached = load_cached_config(self.cache_dir)
        if cached:
            log.info("Laatst bekende playlist uit cache geladen (revisie %s).", cached.get("revision"))
            self.apply(cached)
        self.start_server()
        self.syncer.start()
        sd_notify("READY=1")
        log.info("Signage-player %s gestart (browserweergave, poort %d).", __version__, self.port)
        while self.running:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("Onverwachte fout in de player: %s", exc)
                self.error(f"Interne fout: {exc}")
                time.sleep(1)
            sd_notify("WATCHDOG=1")
            time.sleep(0.5)
        sd_notify("STOPPING=1")
        self.syncer.stop()
        self.stop_browser()
        if self.server:
            self.server.shutdown()

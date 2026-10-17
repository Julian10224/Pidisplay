"""API: dashboardstatus, instellingen, systeemacties, logboeken en back-ups."""

import datetime
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time

from flask import jsonify, request, send_file

from . import __version__, backup, db, media, sysinfo
from .app import ApiError, admin_urls, body, get_db, refresh_standby, state
from .routes_players import player_to_json

log = logging.getLogger("signage.system")

HOSTNAME_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_backup_lock = threading.Lock()


def _settings_json(conn):
    s = db.get_settings(conn)
    return {"system_name": s["system_name"], "default_image_duration": s["default_image_duration"],
            "default_playlist_id": s["default_playlist_id"], "volume": s["volume"],
            "scale_mode": s.get("scale_mode", "cover"), "optimize_videos": bool(s.get("optimize_videos", True))}


def compute_warnings(conn, cfg, st, players):
    warnings = []
    d = sysinfo.disk(cfg.data_dir)
    if d:
        if d["free"] < cfg.min_free_bytes:
            warnings.append({"level": "error", "text": "Opslag bijna vol: uploads worden geweigerd."})
        elif d["free"] < cfg.min_free_bytes * 2:
            warnings.append({"level": "warning", "text": "Weinig vrije opslagruimte."})
    for p in players:
        if not p["enabled"]:
            continue   # server zonder lokale player (--role server)
        if not p["online"]:
            warnings.append({"level": "error" if p["kind"] == "local" else "warning",
                             "text": f"Player '{p['name']}' meldt zich niet (offline of gestopt)."})
            continue
        status = p["status"] or {}
        if status.get("state") == "error":
            warnings.append({"level": "error", "text": f"Player '{p['name']}': {status.get('message') or 'fout'}"})
        for e in (status.get("errors") or [])[-3:]:
            warnings.append({"level": "warning", "text": f"{p['name']}: {e}"})
        if not p["effective_playlist"]:
            warnings.append({"level": "info", "text": f"Player '{p['name']}' heeft geen actieve playlist; "
                                                      "het standby-scherm wordt getoond."})
    missing = 0
    for row in conn.execute("SELECT * FROM media"):
        if not media.playback_available(cfg, row):
            missing += 1
    if missing:
        warnings.append({"level": "warning", "text": f"{missing} mediabestand(en) ontbreken op de opslag."})
    thr = sysinfo.throttled()
    if thr:
        if thr["under_voltage_now"]:
            warnings.append({"level": "error", "text": "Onderspanning gedetecteerd: gebruik een goede 5V/2,5A voeding."})
        elif thr["under_voltage_occurred"]:
            warnings.append({"level": "warning", "text": "Sinds het opstarten is onderspanning opgetreden."})
        if thr["throttled_now"]:
            warnings.append({"level": "warning", "text": "De processor wordt teruggeschakeld (warmte of voeding)."})
    temp = sysinfo.cpu_temperature()
    if temp and temp >= 80:
        warnings.append({"level": "warning", "text": f"Processortemperatuur is hoog ({temp} °C)."})
    return warnings


def _renderer_info(cfg):
    from .config import read_player_config
    pcfg = read_player_config(cfg.local_player_config)
    fails = 0
    try:
        with open(os.path.join(pcfg.get("cache_dir"), "browser-failures"), encoding="utf-8") as fh:
            fails = int(fh.read().strip() or 0)
    except (OSError, ValueError):
        pass
    import shutil as _sh
    browser_installed = bool(_sh.which("cage") and (_sh.which("chromium") or _sh.which("chromium-browser")))
    # Uitvoer van de laatste mislukte start (geschreven door signage-player-launch).
    last_failure, last_failure_at = None, None
    path = os.path.join(pcfg.get("cache_dir"), "browser-last-failure.log")
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            last_failure = fh.read()[-30000:]
        last_failure_at = os.path.getmtime(path)
    except OSError:
        pass
    return {"setting": (pcfg.get("renderer") or "auto").strip().lower(), "browser_failures": fails,
            "resolution": (pcfg.get("browser_resolution") or "auto").strip().lower(),
            "fallback_active": fails >= 3, "browser_installed": browser_installed,
            "last_failure": last_failure, "last_failure_at": last_failure_at}


def _parse_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def register(app):

    @app.get("/api/status")
    def status():
        st = state()
        cfg = st.cfg
        conn = get_db()
        settings = db.get_settings(conn)
        players = [player_to_json(conn, r, settings, st)
                   for r in conn.execute("SELECT * FROM players ORDER BY kind = 'remote', name")]
        default = None
        if settings.get("default_playlist_id"):
            r = conn.execute("SELECT id, name FROM playlists WHERE id = ?",
                             (settings["default_playlist_id"],)).fetchone()
            default = dict(r) if r else None
        return jsonify(
            server={"running": True, "version": __version__, "uptime": time.time() - st.started_at},
            system={"uptime": sysinfo.system_uptime(), "load": sysinfo.load_average(),
                    "memory": sysinfo.memory(), "temperature": sysinfo.cpu_temperature(),
                    "throttled": sysinfo.throttled(), "hostname": sysinfo.hostname(),
                    "model": sysinfo.pi_model()},
            disk=sysinfo.disk(cfg.data_dir),
            network={"urls": admin_urls(cfg), "addresses": sysinfo.ip_addresses(), "port": cfg.port},
            players=players,
            settings=_settings_json(conn),
            default_playlist=default,
            counts={"media": conn.execute("SELECT COUNT(*) FROM media").fetchone()[0],
                    "playlists": conn.execute("SELECT COUNT(*) FROM playlists").fetchone()[0]},
            warnings=compute_warnings(conn, cfg, st, players),
            time=time.time(),
        )

    # -- instellingen -----------------------------------------------------------
    @app.get("/api/settings")
    def settings_get():
        return jsonify(settings=_settings_json(get_db()))

    @app.put("/api/settings")
    def settings_put():
        data = body()
        conn = get_db()
        changes = {}
        if "system_name" in data:
            name = re.sub(r"[\x00-\x1f\x7f]", "", str(data["system_name"])).strip()[:60]
            if not name:
                raise ApiError(400, "Systeemnaam mag niet leeg zijn.")
            changes["system_name"] = name
        if "default_image_duration" in data:
            try:
                dur = float(data["default_image_duration"])
            except (TypeError, ValueError):
                raise ApiError(400, "Ongeldige standaardduur.")
            if not 1 <= dur <= 86400:
                raise ApiError(400, "Standaardduur moet tussen 1 en 86400 seconden liggen.")
            changes["default_image_duration"] = dur
        if "scale_mode" in data:
            if data["scale_mode"] not in db.SCALE_MODES:
                raise ApiError(400, "Ongeldige schaalmodus.")
            changes["scale_mode"] = data["scale_mode"]
        if "optimize_videos" in data:
            changes["optimize_videos"] = bool(data["optimize_videos"])
        if "volume" in data:
            try:
                vol = int(data["volume"])
            except (TypeError, ValueError):
                raise ApiError(400, "Ongeldig volume.")
            changes["volume"] = max(0, min(100, vol))
        if "default_playlist_id" in data:
            pid = data["default_playlist_id"]
            if pid in (None, ""):
                changes["default_playlist_id"] = None
            else:
                from .routes_playlists import get_playlist_row, playlist_problems
                row = get_playlist_row(conn, pid)
                problems = playlist_problems(conn, state().cfg, row["id"])
                if problems:
                    raise ApiError(422, "Playlist kan niet worden geactiveerd.", problems=problems)
                changes["default_playlist_id"] = row["id"]
        with db.Tx(conn):
            for k, v in changes.items():
                db.set_setting(conn, k, v)
        if "system_name" in changes:
            refresh_standby(state(), force=True)
        if changes.get("optimize_videos"):
            state().optimizer.scan()   # video's die nog niet bekeken zijn alsnog omzetten
        return jsonify(settings=_settings_json(conn))

    # -- systeeminformatie en -acties ------------------------------------------
    @app.get("/api/system/info")
    def system_info():
        cfg = state().cfg
        return jsonify(
            version=__version__, model=sysinfo.pi_model(), os=sysinfo.os_name(),
            kernel=platform.release(), arch=platform.machine(), python=platform.python_version(),
            hostname=sysinfo.hostname(), addresses=sysinfo.ip_addresses(), urls=admin_urls(cfg),
            port=cfg.port, bind=cfg.bind, allow_public_clients=cfg.allow_public_clients,
            config_path=cfg.path, data_dir=cfg.data_dir, time=sysinfo.time_status(),
            local_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            player_service={"active": sysinfo.service_active("signage-player.service"),
                            "enabled": sysinfo.service_enabled("signage-player.service")},
            max_upload_mb=cfg.max_upload_bytes // (1024 * 1024),
            renderer=_renderer_info(cfg),
            priv_available=bool(cfg.priv_helper and os.path.exists(cfg.priv_helper)),
        )

    @app.get("/api/system/timezones")
    def system_timezones():
        return jsonify(timezones=sysinfo.timezones())

    @app.post("/api/system/hostname")
    def system_hostname():
        name = str(body().get("hostname", "")).strip().lower()
        if not HOSTNAME_RE.match(name):
            raise ApiError(400, "Hostnaam: 1-63 tekens, alleen a-z, 0-9 en '-', niet beginnen/eindigen met '-'.")
        out = state().priv("set-hostname", name)
        sysinfo.invalidate()
        threading.Thread(target=refresh_standby, args=(state(), True), daemon=True).start()
        return jsonify(ok=True, message=out)

    @app.post("/api/system/timezone")
    def system_timezone():
        tz = str(body().get("timezone", "")).strip()
        if not re.match(r"^[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+){0,2}$", tz) or tz not in sysinfo.timezones():
            raise ApiError(400, "Onbekende tijdzone.")
        state().priv("set-timezone", tz)
        sysinfo.invalidate()
        return jsonify(ok=True)

    @app.post("/api/system/ntp")
    def system_ntp():
        enabled = _parse_bool(body().get("enabled"))
        state().priv("set-ntp", "on" if enabled else "off")
        sysinfo.invalidate()
        return jsonify(ok=True)

    @app.post("/api/system/time")
    def system_time():
        value = str(body().get("datetime", "")).strip().replace("T", " ")
        try:
            dt = datetime.datetime.strptime(value[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            raise ApiError(400, "Gebruik het formaat JJJJ-MM-DD UU:MM.")
        state().priv("set-time", dt.strftime("%Y-%m-%d %H:%M:00"))
        sysinfo.invalidate()
        return jsonify(ok=True)

    @app.post("/api/system/player")
    def system_player():
        action = str(body().get("action", ""))
        if action not in ("restart", "enable", "disable"):
            raise ApiError(400, "Ongeldige actie.")
        state().priv("player", action)
        return jsonify(ok=True)

    @app.post("/api/system/resolution")
    def system_resolution():
        value = str(body().get("resolution", ""))
        if value not in ("auto", "1280x720", "1920x1080"):
            raise ApiError(400, "Ongeldige resolutie.")
        state().priv("set-resolution", value, timeout=60)
        return jsonify(ok=True, renderer=_renderer_info(state().cfg))

    @app.post("/api/system/renderer")
    def system_renderer():
        value = str(body().get("renderer", ""))
        if value not in ("auto", "browser", "mpv", "reset"):
            raise ApiError(400, "Ongeldige weergave.")
        state().priv("set-renderer", value, timeout=60)
        return jsonify(ok=True, renderer=_renderer_info(state().cfg))

    @app.post("/api/system/reboot")
    def system_reboot():
        state().priv("reboot")
        return jsonify(ok=True)

    @app.get("/api/system/logs")
    def system_logs():
        unit = request.args.get("unit", "all")
        try:
            lines = max(10, min(1000, int(request.args.get("lines", 200))))
        except ValueError:
            lines = 200
        units = {"server": ["signage-server.service"], "player": ["signage-player.service"],
                 "all": ["signage-server.service", "signage-player.service"]}.get(unit)
        if units is None:
            raise ApiError(400, "Onbekend logboek.")
        # Per service: de systemd-meldingen (UNIT), de regels van de service zelf
        # (_SYSTEMD_UNIT) en, voor de player, de vaste logboeknaam (zie signage-player.service).
        cmd = ["journalctl", "--no-pager", "-o", "short-iso", "-n", str(lines)]
        matches = []
        for u in units:
            matches += [f"_SYSTEMD_UNIT={u}", f"UNIT={u}"]
            if u == "signage-player.service":
                matches.append("SYSLOG_IDENTIFIER=signage-player")
        for i, m in enumerate(matches):
            cmd += (["+"] if i else []) + [m]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=10, check=False)
            text = res.stdout.decode("utf-8", "replace")
            if res.returncode != 0 and not text:
                text = res.stderr.decode("utf-8", "replace")
        except (OSError, subprocess.TimeoutExpired) as exc:
            text = f"Logboek kon niet worden gelezen: {exc}"
        return jsonify(log=text)

    # -- back-ups ----------------------------------------------------------------
    @app.get("/api/backups")
    def backups_list():
        cfg = state().cfg
        return jsonify(backups=backup.list_backups(cfg), free=shutil.disk_usage(cfg.backups_dir).free)

    @app.post("/api/backups")
    def backups_create():
        cfg = state().cfg
        include_media = _parse_bool(body().get("include_media", False))
        if not _backup_lock.acquire(blocking=False):
            raise ApiError(409, "Er wordt al een back-up gemaakt.")
        try:
            name = backup.create_backup(cfg, include_media=include_media)
        except backup.BackupError as exc:
            raise ApiError(507, str(exc))
        finally:
            _backup_lock.release()
        log.info("Back-up gemaakt: %s", name)
        return jsonify(name=name), 201

    @app.get("/api/backups/<name>")
    def backups_download(name):
        try:
            path = backup.backup_path(state().cfg, name)
        except backup.BackupError as exc:
            raise ApiError(404, str(exc))
        return send_file(path, mimetype="application/gzip", as_attachment=True, download_name=name)

    @app.delete("/api/backups/<name>")
    def backups_delete(name):
        try:
            path = backup.backup_path(state().cfg, name)
        except backup.BackupError as exc:
            raise ApiError(404, str(exc))
        os.unlink(path)
        return jsonify(ok=True)

    def _restart_after_restore():
        st = state()
        try:
            st.priv("restart-server")
            return "De server herstart nu om de back-up terug te zetten."
        except ApiError:
            return "Back-up klaargezet. Herstart de server om het herstel uit te voeren."

    @app.post("/api/backups/restore")
    def backups_restore():
        cfg = state().cfg
        existing = request.args.get("name")
        if existing:
            try:
                src = backup.backup_path(cfg, existing)
                backup.stage_restore(cfg, src)
            except backup.BackupError as exc:
                raise ApiError(422, str(exc))
        else:
            length = request.content_length or 0
            if length <= 0:
                raise ApiError(400, "Geen back-upbestand ontvangen.")
            if shutil.disk_usage(cfg.tmp_dir).free - 2 * length < cfg.min_free_bytes // 2:
                raise ApiError(507, "Onvoldoende vrije ruimte om deze back-up te verwerken.")
            tmp = os.path.join(cfg.tmp_dir, f"upload-restore-{int(time.time())}.tar.gz")
            try:
                with open(tmp, "wb") as out:
                    received = 0
                    while True:
                        chunk = request.stream.read(1024 * 1024)
                        if not chunk:
                            break
                        received += len(chunk)
                        out.write(chunk)
                if received != length:
                    raise ApiError(400, "Back-up onvolledig ontvangen.")
                try:
                    backup.stage_restore(cfg, tmp)
                except backup.BackupError as exc:
                    raise ApiError(422, str(exc))
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        log.info("Herstel klaargezet.")
        return jsonify(ok=True, message=_restart_after_restore())

"""Flask-applicatie: opbouw, authenticatie en gedeelde hulpfuncties."""

import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
import subprocess
import threading
import time
from datetime import timedelta

from flask import Flask, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from . import __version__, backup, db, standby, sysinfo
from .config import Config, read_player_config

log = logging.getLogger("signage.server")

PASSWORD_METHOD = "pbkdf2:sha256:300000"
USERNAME_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


class ApiError(Exception):
    def __init__(self, status, message, **extra):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra


class State:
    """Gedeelde, procesbrede toestand van de server."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.started_at = time.time()
        self.lock = threading.Lock()
        self.standby_lock = threading.Lock()
        self.live = {}         # player_id -> {"status": {...}, "received": ts}
        self.last_seen_written = {}
        self.standby_sha = None
        self.standby_key = None
        self.failed_logins = {}  # ip -> [count, locked_until]
        # Live bediening (vorige/volgende/naar item): opdrachten per player, alleen in het
        # geheugen. Het 'epoch' verandert bij elke serverstart, zodat een player na een
        # herstart nooit oude opdrachten uitvoert.
        self.control = {}        # player_id -> {"seq": n, "cmds": [...]}
        self.control_cv = threading.Condition()
        self.control_epoch = secrets.token_hex(4)
        self.control_waiters = 0

    # -- privileged helper --------------------------------------------------
    def priv(self, *args, timeout=30):
        helper = self.cfg.priv_helper
        if not helper or not os.path.exists(helper):
            raise ApiError(501, "Systeemacties zijn niet beschikbaar (privileged helper ontbreekt).")
        try:
            res = subprocess.run(["sudo", "-n", helper, *args], capture_output=True,
                                 timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise ApiError(504, "Systeemactie duurde te lang.")
        out = (res.stdout + res.stderr).decode("utf-8", "replace").strip()
        if res.returncode != 0:
            raise ApiError(400 if res.returncode == 2 else 500, out or "Systeemactie mislukt.")
        return out


# ---------------------------------------------------------------------------
# Hulpfuncties
# ---------------------------------------------------------------------------

def state():
    from flask import current_app
    return current_app.extensions["signage"]


def get_db():
    if "db" not in g:
        g.db = db.connect(state().cfg.db_path)
    return g.db


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "Verwacht een JSON-object.")
    return data


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read_or_create_secret(path, nbytes=32):
    try:
        with open(path, "rb") as fh:
            data = fh.read()
        if len(data) >= 16:
            return data
    except FileNotFoundError:
        pass
    data = secrets.token_bytes(nbytes)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return data


def setup_required(conn):
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def current_setup_code(cfg):
    """Eenmalige setup-code; bestaat alleen zolang er geen beheerder is."""
    try:
        with open(cfg.setup_code_path, "r", encoding="utf-8") as fh:
            code = fh.read().strip()
            if code:
                return code
    except FileNotFoundError:
        pass
    digits = "".join(secrets.choice("0123456789") for _ in range(8))
    code = f"{digits[:4]}-{digits[4:]}"
    fd = os.open(cfg.setup_code_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(code)
    return code


def admin_urls(cfg):
    port = "" if cfg.port == 80 else f":{cfg.port}"
    urls = [f"http://{sysinfo.hostname()}.local{port}"]
    for a in sysinfo.ip_addresses():
        urls.append(f"http://{a['address']}{port}")
    return urls


def refresh_standby(st, force=False):
    """Teken het standby-scherm opnieuw als naam, adressen of setup-status wijzigen."""
    with st.standby_lock:
        return _refresh_standby_locked(st, force)


def _refresh_standby_locked(st, force):
    cfg = st.cfg
    conn = db.connect(cfg.db_path)
    try:
        settings = db.get_settings(conn)
        need_setup = setup_required(conn)
    finally:
        conn.close()
    code = current_setup_code(cfg) if need_setup else None
    urls = admin_urls(cfg)
    key = (settings["system_name"], tuple(urls), code)
    if not force and key == st.standby_key and os.path.exists(cfg.standby_path):
        return st.standby_sha
    message = "Installatie bijna klaar." if need_setup else None
    st.standby_sha = standby.render(cfg.standby_path, settings["system_name"], urls, code, message)
    st.standby_key = key
    return st.standby_sha


def _client_allowed(addr, allow_public):
    if allow_public:
        return True
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


# ---------------------------------------------------------------------------
# Applicatie
# ---------------------------------------------------------------------------

def create_app(cfg=None, start_background=True):
    cfg = cfg or Config()
    cfg.ensure_dirs()
    backup.apply_pending_restore(cfg, log)
    existing = db.schema_version(cfg.db_path) if os.path.exists(cfg.db_path) else None
    if existing is not None and existing < db.SCHEMA_VERSION:
        # Altijd eerst een back-up vóór een databasemigratie.
        name = backup.create_backup(cfg, include_media=False, prefix="pre-migration-")
        log.info("Database wordt bijgewerkt van versie %s naar %s; back-up gemaakt: %s",
                 existing, db.SCHEMA_VERSION, name)
    db.init_db(cfg.db_path)
    _cleanup_incoming(cfg)
    repair_html_projects(cfg)

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    app = Flask(__name__, static_folder=None)
    app.secret_key = _read_or_create_secret(cfg.secret_key_path)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="signage_session",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=cfg.session_hours),
        MAX_CONTENT_LENGTH=cfg.max_upload_bytes + 1024 * 1024,
        JSON_SORT_KEYS=False,
    )
    st = State(cfg)
    app.extensions["signage"] = st

    _sync_local_player(cfg)
    try:
        refresh_standby(st, force=True)
    except Exception as exc:  # noqa: BLE001
        log.error("Standby-scherm kon niet worden gemaakt: %s", exc)

    # -- request hooks -------------------------------------------------------
    @app.before_request
    def _guard():
        if not _client_allowed(request.remote_addr or "", cfg.allow_public_clients):
            return jsonify(error="Toegang alleen vanaf het lokale netwerk."), 403
        path = request.path
        if not path.startswith("/api/"):
            return None
        if path.startswith("/api/player/v1/") or path.startswith("/api/auth/"):
            return None
        if not session.get("uid"):
            return jsonify(error="Niet ingelogd.", code="auth"), 401
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            token = request.headers.get("X-CSRF-Token", "")
            if not token or not hmac.compare_digest(token, session.get("csrf", "")):
                return jsonify(error="Ongeldige sessie, laad de pagina opnieuw.", code="csrf"), 403
        return None

    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.teardown_appcontext
    def _close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.errorhandler(ApiError)
    def _api_error(exc):
        return jsonify(error=exc.message, **exc.extra), exc.status

    @app.errorhandler(413)
    def _too_large(_exc):
        return jsonify(error="Bestand is groter dan de maximale uploadgrootte."), 413

    @app.errorhandler(404)
    def _not_found(_exc):
        if request.path.startswith("/api/"):
            return jsonify(error="Niet gevonden."), 404
        return send_from_directory(static_dir, "index.html")

    @app.errorhandler(500)
    def _server_error(_exc):
        return jsonify(error="Interne serverfout; zie het logboek."), 500

    # -- authenticatie ----------------------------------------------------------
    @app.get("/api/auth/state")
    def auth_state():
        conn = get_db()
        need = setup_required(conn)
        out = {"setup_required": need, "logged_in": False, "version": __version__}
        uid = session.get("uid")
        if uid and not need:
            user = conn.execute("SELECT id, username FROM users WHERE id = ?", (uid,)).fetchone()
            if user:
                if "csrf" not in session:
                    session["csrf"] = secrets.token_urlsafe(32)
                out.update(logged_in=True, user=user["username"], csrf=session["csrf"])
            else:
                session.clear()
        out["system_name"] = db.get_settings(conn)["system_name"]
        return jsonify(out)

    def _check_throttle():
        ip = request.remote_addr or "?"
        rec = st.failed_logins.get(ip)
        if rec and rec[1] > time.time():
            raise ApiError(429, f"Te veel mislukte pogingen. Probeer het over {int(rec[1] - time.time()) + 1} s opnieuw.")
        return ip

    def _register_failure(ip):
        rec = st.failed_logins.setdefault(ip, [0, 0])
        rec[0] += 1
        if rec[0] >= 5:
            rec[1] = time.time() + min(300, 15 * 2 ** (rec[0] - 5))

    def _login(user_id):
        session.clear()
        session.permanent = True
        session["uid"] = user_id
        session["csrf"] = secrets.token_urlsafe(32)

    def _validate_credentials(data):
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        if not (3 <= len(username) <= 32) or not set(username) <= USERNAME_CHARS:
            raise ApiError(400, "Gebruikersnaam: 3-32 tekens, alleen letters, cijfers, punt, - en _.")
        if len(password) < 8:
            raise ApiError(400, "Wachtwoord moet minimaal 8 tekens hebben.")
        if len(password) > 256:
            raise ApiError(400, "Wachtwoord is te lang.")
        return username, password

    @app.post("/api/auth/setup")
    def auth_setup():
        ip = _check_throttle()
        data = body()
        conn = get_db()
        if not setup_required(conn):
            raise ApiError(409, "De beheerder is al aangemaakt.")
        code = str(data.get("setup_code", "")).strip().replace(" ", "")
        expected = current_setup_code(cfg)
        if not hmac.compare_digest(code.replace("-", ""), expected.replace("-", "")):
            _register_failure(ip)
            raise ApiError(403, "Setup-code klopt niet. De code staat op het aangesloten scherm.")
        username, password = _validate_credentials(data)
        with db.Tx(conn):
            if not setup_required(conn):
                raise ApiError(409, "De beheerder is al aangemaakt.")
            cur = conn.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                               (username, generate_password_hash(password, method=PASSWORD_METHOD), db.now()))
        try:
            os.unlink(cfg.setup_code_path)
        except FileNotFoundError:
            pass
        _login(cur.lastrowid)
        threading.Thread(target=refresh_standby, args=(st,), daemon=True).start()
        log.info("Beheerder '%s' aangemaakt.", username)
        return jsonify(ok=True)

    @app.post("/api/auth/login")
    def auth_login():
        ip = _check_throttle()
        data = body()
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        conn = get_db()
        user = conn.execute("SELECT id, password_hash FROM users WHERE username = ?", (username,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            _register_failure(ip)
            log.warning("Mislukte login voor '%s' vanaf %s", username[:32], ip)
            raise ApiError(401, "Gebruikersnaam of wachtwoord onjuist.")
        st.failed_logins.pop(ip, None)
        _login(user["id"])
        return jsonify(ok=True)

    @app.post("/api/auth/logout")
    def auth_logout():
        session.clear()
        return jsonify(ok=True)

    @app.post("/api/auth/password")
    def auth_password():
        if not session.get("uid"):
            raise ApiError(401, "Niet ingelogd.")
        token = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(token, session.get("csrf", "")):
            raise ApiError(403, "Ongeldige sessie.")
        data = body()
        conn = get_db()
        user = conn.execute("SELECT id, username, password_hash FROM users WHERE id = ?",
                            (session["uid"],)).fetchone()
        if not user or not check_password_hash(user["password_hash"], str(data.get("current", ""))):
            raise ApiError(400, "Huidig wachtwoord onjuist.")
        _u, new_pw = _validate_credentials({"username": user["username"], "password": data.get("new", "")})
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (generate_password_hash(new_pw, method=PASSWORD_METHOD), user["id"]))
        return jsonify(ok=True)

    # -- routes uit de andere modules -------------------------------------------
    from . import routes_media, routes_playlists, routes_players, routes_system, routes_update
    routes_media.register(app)
    routes_playlists.register(app)
    routes_players.register(app)
    routes_system.register(app)
    routes_update.register(app)

    # -- statische frontend ------------------------------------------------------
    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.get("/static/<path:filename>")
    def static_files(filename):
        resp = send_from_directory(static_dir, filename, max_age=300)
        return resp

    if start_background:
        threading.Thread(target=_background_loop, args=(st,), daemon=True, name="signage-bg").start()
        threading.Thread(target=regenerate_renditions, args=(cfg,), daemon=True, name="signage-rend").start()
    else:
        regenerate_renditions(cfg)
    return app


def repair_html_projects(cfg):
    """Pak HTML-projecten opnieuw uit als de map ontbreekt maar het origineel er is
    (bijvoorbeeld na het terugzetten van een back-up met media)."""
    from . import htmlpkg
    conn = db.connect(cfg.db_path)
    try:
        for row in conn.execute("SELECT * FROM media WHERE kind = 'html' AND playback_dir = 'html'"):
            dest = os.path.join(cfg.html_dir, row["playback_name"])
            src = os.path.join(cfg.files_dir, row["file_name"])
            if not os.path.isdir(dest) and os.path.isfile(src):
                try:
                    htmlpkg.extract_project(src, row["file_name"], dest)
                    log.info("HTML-project '%s' opnieuw uitgepakt.", row["name"])
                except htmlpkg.HtmlError as exc:
                    log.warning("HTML-project '%s' kon niet worden uitgepakt: %s", row["name"], exc)
    finally:
        conn.close()


def regenerate_renditions(cfg):
    """Eenmalig na de update naar 1.1: afspeelversies maken die het scherm beeldvullend
    kunnen bedekken (voorheen werden afbeeldingen alleen 'passend' verkleind)."""
    from . import media as media_mod
    conn = db.connect(cfg.db_path)
    try:
        if db.get_meta(conn, "renditions_version", "2") == "2":
            return
        rows = conn.execute("SELECT * FROM media WHERE kind = 'image'").fetchall()
        done = 0
        for row in rows:
            src = os.path.join(cfg.files_dir, row["file_name"])
            if not os.path.exists(src):
                continue
            try:
                rend, _w, _h = media_mod.make_image_rendition(src, os.path.join(cfg.renditions_dir, row["id"]),
                                                              cfg.max_image_size)
            except Exception as exc:  # noqa: BLE001
                log.warning("Afspeelversie van %s kon niet opnieuw worden gemaakt: %s", row["name"], exc)
                continue
            old = os.path.join(cfg.renditions_dir, row["playback_name"])
            new_name = os.path.basename(rend)
            conn.execute("UPDATE media SET playback_name = ?, playback_sha256 = ?, playback_size = ? WHERE id = ?",
                         (new_name, media_mod.sha256_file(rend), os.path.getsize(rend), row["id"]))
            if old != rend and os.path.exists(old):
                os.unlink(old)
            done += 1
        db.set_meta(conn, "renditions_version", "2")
        if done:
            log.info("%d afspeelversie(s) bijgewerkt voor beeldvullende weergave.", done)
    finally:
        conn.close()


def _cleanup_incoming(cfg):
    """Verwijder restanten van uploads die tijdens een stroomstoring werden onderbroken."""
    for d in (cfg.incoming_dir, cfg.tmp_dir):
        for f in os.listdir(d):
            p = os.path.join(d, f)
            try:
                if os.path.isfile(p):
                    os.unlink(p)
                else:
                    import shutil
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass
    for f in os.listdir(cfg.html_dir):
        if f.startswith(".tmp-"):
            import shutil
            shutil.rmtree(os.path.join(cfg.html_dir, f), ignore_errors=True)
    for d in (cfg.renditions_dir, cfg.thumbs_dir, cfg.files_dir):
        for f in os.listdir(d):
            if f.endswith(".tmp"):
                try:
                    os.unlink(os.path.join(d, f))
                except OSError:
                    pass


def _sync_local_player(cfg):
    """Zorg dat de lokale player bestaat en dat het token overeenkomt met player.ini."""
    pcfg = read_player_config(cfg.local_player_config)
    token = (pcfg.get("token") or "").strip()
    conn = db.connect(cfg.db_path)
    try:
        row = conn.execute("SELECT id, token_hash FROM players WHERE id = 'local'").fetchone()
        token_hash = hash_token(token) if token else "!disabled"
        if row is None:
            conn.execute(
                "INSERT INTO players (id, name, kind, token_hash, created_at) VALUES ('local', ?, 'local', ?, ?)",
                ("Deze Raspberry Pi", token_hash, db.now()))
        elif row["token_hash"] != token_hash:
            conn.execute("UPDATE players SET token_hash = ? WHERE id = 'local'", (token_hash,))
    finally:
        conn.close()


def _background_loop(st):
    while True:
        time.sleep(30)
        try:
            refresh_standby(st)
        except Exception as exc:  # noqa: BLE001
            log.warning("Standby verversen mislukt: %s", exc)


"""API: opstartscherm (Plymouth-thema 'signage': logo in het midden met een laadbalk).

De server maakt van het gekozen beeld een PNG in <data_dir>/boot/logo.png (als gebruiker
'signage'); signage-priv zet daar als root het Plymouth-thema van, gedraaid volgens de
schermstand van de lokale player. Zonder gekozen logo: het standaardlogo.
"""

import logging
import os
import threading

from flask import jsonify, send_file

from . import db, media
from .app import ApiError, body, get_db, state

log = logging.getLogger("signage.splash")

THEME_DIR = "/usr/share/plymouth/themes/signage"
DEFAULT_LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "boot", "logo-default.png")
MAX_SIDE = 1200


def logo_path(cfg):
    return os.path.join(cfg.data_dir, "boot", "logo.png")


def render_logo(cfg, row):
    """Beeld uit de mediabibliotheek omzetten naar een PNG voor het opstartscherm."""
    from PIL import Image
    paths = media.media_paths(cfg, row)
    src = paths["file"] if paths["file"] and os.path.isfile(paths["file"]) else paths["playback"]
    if not src or not os.path.isfile(src):
        raise ApiError(409, "Het bestand van dit beeld ontbreekt.")
    out = logo_path(cfg)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    try:
        with Image.open(src) as im:
            im = im.convert("RGBA")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            im.save(out + ".tmp", "PNG")
    except (OSError, ValueError) as exc:
        raise ApiError(400, f"Dit beeld kan niet als logo worden gebruikt: {exc}")
    os.replace(out + ".tmp", out)


def apply(st):
    """Thema bijwerken via signage-priv. Geeft een melding terug (ook als het niet kan)."""
    try:
        return st.priv("splash", timeout=120) or "Opstartscherm bijgewerkt."
    except ApiError as exc:
        if exc.status == 501:
            return "Opgeslagen; wordt toegepast zodra de systeemhulp beschikbaar is (installatiescript)."
        raise


def apply_in_background(st):
    """Na een andere schermstand: thema opnieuw draaien, zonder het verzoek op te houden."""
    def run():
        try:
            apply(st)
        except Exception as exc:  # noqa: BLE001
            log.warning("Opstartscherm bijwerken mislukt: %s", exc)
    threading.Thread(target=run, daemon=True, name="signage-splash").start()


def info(conn, cfg):
    settings = db.get_settings(conn)
    mid = settings.get("boot_logo_media_id")
    logo = None
    if mid:
        row = conn.execute("SELECT id, name FROM media WHERE id = ?", (mid,)).fetchone()
        logo = {"id": mid, "name": row["name"] if row else None, "deleted": row is None}
    path = logo_path(cfg)
    version = int(os.path.getmtime(path)) if os.path.isfile(path) else 0
    return {"installed": os.path.isdir(THEME_DIR), "logo": logo,
            "preview_url": f"/api/system/splash/logo.png?v={version}"}


def register(app):

    @app.get("/api/system/splash")
    def splash_get():
        return jsonify(splash=info(get_db(), state().cfg))

    @app.get("/api/system/splash/logo.png")
    def splash_logo():
        path = logo_path(state().cfg)
        return send_file(path if os.path.isfile(path) else DEFAULT_LOGO, mimetype="image/png", max_age=0)

    @app.put("/api/system/splash")
    def splash_put():
        st = state()
        cfg = st.cfg
        conn = get_db()
        mid = body().get("media_id")
        if mid in (None, ""):
            try:
                os.unlink(logo_path(cfg))
            except FileNotFoundError:
                pass
            db.set_setting(conn, "boot_logo_media_id", None)
        else:
            row = conn.execute("SELECT * FROM media WHERE id = ?", (str(mid),)).fetchone()
            if row is None:
                raise ApiError(404, "Beeld niet gevonden.")
            if row["kind"] != "image":
                raise ApiError(400, "Kies een foto of afbeelding (bij voorkeur een PNG met transparante achtergrond).")
            render_logo(cfg, row)
            db.set_setting(conn, "boot_logo_media_id", row["id"])
        message = apply(st)
        return jsonify(splash=info(conn, cfg), message=message)

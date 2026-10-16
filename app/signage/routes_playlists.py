"""API: playlists maken, bewerken, valideren en activeren."""

import logging
import os
import re
import sqlite3

from flask import jsonify, request

from . import db, media
from .app import ApiError, body, get_db, state
from .routes_media import media_row_to_json

log = logging.getLogger("signage.playlists")

REPEAT_MODES = ("loop", "once_hold_last", "once_then_default")
MAX_ITEMS = 500


def _clean_name(value):
    name = re.sub(r"[\x00-\x1f\x7f]", "", str(value or "")).strip()
    if not name:
        raise ApiError(400, "Geef de playlist een naam.")
    if len(name) > 80:
        raise ApiError(400, "Naam is te lang (maximaal 80 tekens).")
    return name


def get_playlist_row(conn, pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        raise ApiError(404, "Playlist niet gevonden.")
    row = conn.execute("SELECT * FROM playlists WHERE id = ?", (pid,)).fetchone()
    if row is None:
        raise ApiError(404, "Playlist niet gevonden.")
    return row


def playlist_items(conn, pid):
    return conn.execute(
        "SELECT pi.id AS item_id, pi.position, pi.duration AS item_duration, m.* "
        "FROM playlist_items pi JOIN media m ON m.id = pi.media_id "
        "WHERE pi.playlist_id = ? ORDER BY pi.position", (pid,)).fetchall()


def playlist_problems(conn, cfg, pid):
    """Lijst met redenen waarom de playlist niet geactiveerd kan worden."""
    problems = []
    items = playlist_items(conn, pid)
    if not items:
        problems.append("De playlist is leeg.")
    for it in items:
        if not os.path.exists(media.media_paths(cfg, it)["playback"]):
            problems.append(f"Bestand ontbreekt op de opslag: {it['name']}")
    return problems


def _assignment_info(conn):
    settings = db.get_settings(conn)
    default_id = settings.get("default_playlist_id")
    assigned = {}
    for r in conn.execute("SELECT id, name, playlist_id FROM players"):
        effective = r["playlist_id"] if r["playlist_id"] is not None else default_id
        if effective is not None:
            assigned.setdefault(effective, []).append({"id": r["id"], "name": r["name"]})
    return default_id, assigned


def _summary(conn, cfg, row, default_id, assigned, settings):
    items = playlist_items(conn, row["id"])
    total = 0.0
    for it in items:
        if it["kind"] == "image":
            total += it["item_duration"] or settings["default_image_duration"]
        else:
            total += it["duration"] or 0
    return {
        "id": row["id"], "name": row["name"], "repeat_mode": row["repeat_mode"], "mute": bool(row["mute"]),
        "item_count": len(items), "total_duration": round(total, 1),
        "is_default": row["id"] == default_id, "players": assigned.get(row["id"], []),
        "thumbs": [it["id"] for it in items[:4]],
        "valid": not playlist_problems(conn, cfg, row["id"]),
        "updated_at": row["updated_at"],
    }


def register(app):

    @app.get("/api/playlists")
    def playlists_list():
        cfg = state().cfg
        conn = get_db()
        settings = db.get_settings(conn)
        default_id, assigned = _assignment_info(conn)
        rows = conn.execute("SELECT * FROM playlists ORDER BY name COLLATE NOCASE").fetchall()
        return jsonify(playlists=[_summary(conn, cfg, r, default_id, assigned, settings) for r in rows])

    @app.post("/api/playlists")
    def playlists_create():
        name = _clean_name(body().get("name"))
        conn = get_db()
        try:
            cur = conn.execute("INSERT INTO playlists (name, created_at, updated_at) VALUES (?, ?, ?)",
                               (name, db.now(), db.now()))
        except sqlite3.IntegrityError:
            raise ApiError(409, "Er bestaat al een playlist met deze naam.")
        return jsonify(id=cur.lastrowid), 201

    @app.get("/api/playlists/<pid>")
    def playlists_get(pid):
        cfg = state().cfg
        conn = get_db()
        row = get_playlist_row(conn, pid)
        settings = db.get_settings(conn)
        default_id, assigned = _assignment_info(conn)
        items = []
        for it in playlist_items(conn, row["id"]):
            m = media_row_to_json(cfg, it)
            items.append({"item_id": it["item_id"], "duration": it["item_duration"], "media": m})
        out = _summary(conn, cfg, row, default_id, assigned, settings)
        out["items"] = items
        out["problems"] = playlist_problems(conn, cfg, row["id"])
        return jsonify(playlist=out)

    @app.put("/api/playlists/<pid>")
    def playlists_update(pid):
        data = body()
        conn = get_db()
        row = get_playlist_row(conn, pid)
        name = _clean_name(data.get("name", row["name"]))
        repeat_mode = data.get("repeat_mode", row["repeat_mode"])
        if repeat_mode not in REPEAT_MODES:
            raise ApiError(400, "Ongeldige herhaalinstelling.")
        mute = 1 if data.get("mute", bool(row["mute"])) else 0
        items = data.get("items")
        clean_items = None
        if items is not None:
            if not isinstance(items, list) or len(items) > MAX_ITEMS:
                raise ApiError(400, f"Items moeten een lijst zijn (maximaal {MAX_ITEMS}).")
            ids = {r["id"]: r["kind"] for r in conn.execute("SELECT id, kind FROM media")}
            clean_items = []
            for i, it in enumerate(items):
                if not isinstance(it, dict) or it.get("media_id") not in ids:
                    raise ApiError(400, f"Item {i + 1} verwijst naar onbekende media.")
                dur = it.get("duration")
                if dur in ("", None) or ids[it["media_id"]] == "video":
                    dur = None
                else:
                    try:
                        dur = float(dur)
                    except (TypeError, ValueError):
                        raise ApiError(400, f"Item {i + 1}: ongeldige duur.")
                    if not (1 <= dur <= 86400):
                        raise ApiError(400, f"Item {i + 1}: duur moet tussen 1 en 86400 seconden liggen.")
                clean_items.append((it["media_id"], dur))
            if not clean_items:
                cfg = state().cfg
                default_id, assigned = _assignment_info(conn)
                if row["id"] in assigned:
                    raise ApiError(422, "Deze playlist is actief op een scherm en mag niet leeg worden. "
                                        "Activeer eerst een andere playlist.")
        try:
            with db.Tx(conn):
                conn.execute("UPDATE playlists SET name = ?, repeat_mode = ?, mute = ?, updated_at = ? WHERE id = ?",
                             (name, repeat_mode, mute, db.now(), row["id"]))
                if clean_items is not None:
                    conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (row["id"],))
                    conn.executemany(
                        "INSERT INTO playlist_items (playlist_id, media_id, position, duration) VALUES (?, ?, ?, ?)",
                        [(row["id"], mid, pos, dur) for pos, (mid, dur) in enumerate(clean_items)])
        except sqlite3.IntegrityError:
            raise ApiError(409, "Er bestaat al een playlist met deze naam.")
        log.info("Playlist '%s' opgeslagen.", name)
        return playlists_get(row["id"])

    @app.delete("/api/playlists/<pid>")
    def playlists_delete(pid):
        conn = get_db()
        row = get_playlist_row(conn, pid)
        default_id, assigned = _assignment_info(conn)
        in_use = row["id"] in assigned
        if in_use and request.args.get("force") != "1":
            raise ApiError(409, "Deze playlist is actief op een of meer schermen.", players=assigned[row["id"]])
        with db.Tx(conn):
            if default_id == row["id"]:
                db.set_setting(conn, "default_playlist_id", None)
            conn.execute("UPDATE players SET playlist_id = NULL WHERE playlist_id = ?", (row["id"],))
            conn.execute("DELETE FROM playlists WHERE id = ?", (row["id"],))
        log.info("Playlist '%s' verwijderd.", row["name"])
        return jsonify(ok=True)

    @app.post("/api/playlists/<pid>/activate")
    def playlists_activate(pid):
        cfg = state().cfg
        conn = get_db()
        row = get_playlist_row(conn, pid)
        target = str(body().get("target", "default"))
        problems = playlist_problems(conn, cfg, row["id"])
        if problems:
            raise ApiError(422, "Playlist kan niet worden geactiveerd.", problems=problems)
        if target == "default":
            db.set_setting(conn, "default_playlist_id", row["id"])
        else:
            cur = conn.execute("UPDATE players SET playlist_id = ? WHERE id = ?", (row["id"], target))
            if cur.rowcount == 0:
                raise ApiError(404, "Player niet gevonden.")
        log.info("Playlist '%s' geactiveerd (%s).", row["name"], target)
        return jsonify(ok=True)

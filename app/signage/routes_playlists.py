"""API: playlists maken, bewerken, valideren en activeren.

Sinds 1.1:
- items kunnen foto's, video's en HTML zijn; duur geldt voor foto's en HTML;
- per item een optionele schaalmodus (anders de globale instelling);
- overgangen: een playlistbrede overgangsvideo, per item te overschrijven
  ('default' = playlist-standaard, 'none' = geen, 'custom' = eigen video).
  Een overgang hoort bij het item ervóór: hij speelt nadat dat item klaar is.
"""

import logging
import re
import sqlite3

from flask import jsonify, request

from . import db, media
from .app import ApiError, body, get_db, state
from .routes_media import media_row_to_json

log = logging.getLogger("signage.playlists")

REPEAT_MODES = ("loop", "once_hold_last", "once_then_default")
TRANSITION_MODES = ("default", "none", "custom")
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
        "SELECT pi.id AS item_id, pi.position, pi.duration AS item_duration, pi.scale_mode AS item_scale_mode, "
        "pi.transition_mode AS item_transition_mode, pi.transition_media_id AS item_transition_media_id, "
        "pi.logo_media_id AS item_logo_media_id, m.* "
        "FROM playlist_items pi JOIN media m ON m.id = pi.media_id "
        "WHERE pi.playlist_id = ? ORDER BY pi.position", (pid,)).fetchall()


def resolve_transition_id(pl_row, item):
    """Media-id van de overgang ná dit item, of None."""
    if not pl_row["transition_enabled"]:
        return None
    mode = item["item_transition_mode"]
    if mode == "none":
        return None
    if mode == "custom":
        return item["item_transition_media_id"]
    return pl_row["transition_media_id"]


def playlist_problems(conn, cfg, pid):
    """Redenen waarom de playlist niet geactiveerd kan worden (blokkerend)."""
    problems = []
    items = playlist_items(conn, pid)
    if not items:
        problems.append("De playlist is leeg.")
    for it in items:
        if not media.playback_available(cfg, it):
            problems.append(f"Bestand ontbreekt op de opslag: {it['name']}")
    return problems


def playlist_warnings(conn, cfg, pl_row):
    """Niet-blokkerende meldingen (een ontbrekende overgang of logo wordt overgeslagen)."""
    warnings = []
    for it in playlist_items(conn, pl_row["id"]):
        if it["item_logo_media_id"]:
            lrow = conn.execute("SELECT * FROM media WHERE id = ?", (it["item_logo_media_id"],)).fetchone()
            if lrow is None or not media.playback_available(cfg, lrow):
                warnings.append(f"Het logo bij '{it['name']}' ontbreekt; het item wordt zonder logo getoond.")
    if not pl_row["transition_enabled"]:
        return warnings
    ids = set()
    if pl_row["transition_media_id"]:
        ids.add(pl_row["transition_media_id"])
    else:
        if any(it["item_transition_mode"] == "default" for it in playlist_items(conn, pl_row["id"])):
            warnings.append("Overgangen staan aan, maar er is geen standaardovergang gekozen.")
    for it in playlist_items(conn, pl_row["id"]):
        if it["item_transition_mode"] == "custom" and it["item_transition_media_id"]:
            ids.add(it["item_transition_media_id"])
    for mid in ids:
        row = conn.execute("SELECT * FROM media WHERE id = ?", (mid,)).fetchone()
        if row is None or not media.playback_available(cfg, row):
            warnings.append("Een overgangsvideo ontbreekt; die overgang wordt overgeslagen.")
    return warnings


def _assignment_info(conn):
    settings = db.get_settings(conn)
    default_id = settings.get("default_playlist_id")
    assigned = {}
    for r in conn.execute("SELECT id, name, playlist_id FROM players"):
        effective = r["playlist_id"] if r["playlist_id"] is not None else default_id
        if effective is not None:
            assigned.setdefault(effective, []).append({"id": r["id"], "name": r["name"]})
    return default_id, assigned


def _item_seconds(it, settings):
    if it["kind"] == "video":
        return it["duration"] or 0
    return it["item_duration"] or settings["default_image_duration"]


def _summary(conn, cfg, row, default_id, assigned, settings):
    items = playlist_items(conn, row["id"])
    total = sum(_item_seconds(it, settings) for it in items)
    durations = {}
    for it in items:
        tid = resolve_transition_id(row, it)
        if tid:
            if tid not in durations:
                t = conn.execute("SELECT duration FROM media WHERE id = ?", (tid,)).fetchone()
                durations[tid] = (t["duration"] or 0) if t else 0
            total += durations[tid]
    return {
        "id": row["id"], "name": row["name"], "repeat_mode": row["repeat_mode"], "mute": bool(row["mute"]),
        "item_count": len(items), "total_duration": round(total, 1),
        "is_default": row["id"] == default_id, "players": assigned.get(row["id"], []),
        "thumbs": [it["id"] for it in items[:4]],
        "valid": not playlist_problems(conn, cfg, row["id"]),
        "updated_at": row["updated_at"],
        "transition_enabled": bool(row["transition_enabled"]),
        "transition_media_id": row["transition_media_id"],
        "new_item_transition": row["new_item_transition"],
        "shuffle": bool(row["shuffle"]),
    }


def _media_json(conn, cfg, media_id):
    if not media_id:
        return None
    row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
    return media_row_to_json(cfg, row) if row else None


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
            items.append({
                "item_id": it["item_id"], "duration": it["item_duration"], "media": m,
                "scale_mode": it["item_scale_mode"],
                "transition_mode": it["item_transition_mode"],
                "transition_media_id": it["item_transition_media_id"],
                "transition_media": _media_json(conn, cfg, it["item_transition_media_id"]),
                "logo_media_id": it["item_logo_media_id"],
                "logo_media": _media_json(conn, cfg, it["item_logo_media_id"]),
            })
        out = _summary(conn, cfg, row, default_id, assigned, settings)
        out["items"] = items
        out["transition_media"] = _media_json(conn, cfg, row["transition_media_id"])
        out["problems"] = playlist_problems(conn, cfg, row["id"])
        out["warnings"] = playlist_warnings(conn, cfg, row)
        return jsonify(playlist=out)

    def _video_id(conn, mid, what):
        if mid in (None, ""):
            return None
        r = conn.execute("SELECT kind FROM media WHERE id = ?", (str(mid),)).fetchone()
        if r is None:
            raise ApiError(400, f"{what}: onbekende video.")
        if r["kind"] != "video":
            raise ApiError(400, f"{what}: kies een videobestand als overgang.")
        return str(mid)

    def _logo_id(conn, mid, what):
        if mid in (None, ""):
            return None
        r = conn.execute("SELECT kind, format FROM media WHERE id = ?", (str(mid),)).fetchone()
        if r is None:
            raise ApiError(400, f"{what}: onbekend logo.")
        if r["kind"] != "image" or r["format"] != "PNG":
            raise ApiError(400, f"{what}: een logo moet een PNG-afbeelding zijn.")
        return str(mid)

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
        t_enabled = 1 if data.get("transition_enabled", bool(row["transition_enabled"])) else 0
        t_media = _video_id(conn, data.get("transition_media_id", row["transition_media_id"]), "Standaardovergang")
        shuffle = 1 if data.get("shuffle", bool(row["shuffle"])) else 0
        new_item_t = data.get("new_item_transition", row["new_item_transition"])
        if new_item_t not in ("default", "none"):
            raise ApiError(400, "Ongeldige standaardinstelling voor nieuwe items.")
        items = data.get("items")
        clean_items = None
        if items is not None:
            if not isinstance(items, list) or len(items) > MAX_ITEMS:
                raise ApiError(400, f"Items moeten een lijst zijn (maximaal {MAX_ITEMS}).")
            kinds = {r["id"]: r["kind"] for r in conn.execute("SELECT id, kind FROM media")}
            clean_items = []
            for i, it in enumerate(items):
                if not isinstance(it, dict) or it.get("media_id") not in kinds:
                    raise ApiError(400, f"Item {i + 1} verwijst naar onbekende media.")
                kind = kinds[it["media_id"]]
                dur = it.get("duration")
                if dur in ("", None) or kind == "video":
                    dur = None
                else:
                    try:
                        dur = float(dur)
                    except (TypeError, ValueError):
                        raise ApiError(400, f"Item {i + 1}: ongeldige duur.")
                    if not (1 <= dur <= 86400):
                        raise ApiError(400, f"Item {i + 1}: duur moet tussen 1 en 86400 seconden liggen.")
                scale = it.get("scale_mode") or None
                if scale is not None and scale not in db.SCALE_MODES:
                    raise ApiError(400, f"Item {i + 1}: ongeldige schaalmodus.")
                tmode = it.get("transition_mode") or "default"
                if tmode not in TRANSITION_MODES:
                    raise ApiError(400, f"Item {i + 1}: ongeldige overganginstelling.")
                tmid = None
                if tmode == "custom":
                    tmid = _video_id(conn, it.get("transition_media_id"), f"Item {i + 1}")
                    if tmid is None:
                        raise ApiError(400, f"Item {i + 1}: kies een overgangsvideo of 'standaard'.")
                logo = _logo_id(conn, it.get("logo_media_id"), f"Item {i + 1}")
                clean_items.append((it["media_id"], dur, scale, tmode, tmid, logo))
            if not clean_items:
                _default_id, assigned = _assignment_info(conn)
                if row["id"] in assigned:
                    raise ApiError(422, "Deze playlist is actief op een scherm en mag niet leeg worden. "
                                        "Activeer eerst een andere playlist.")
        try:
            with db.Tx(conn):
                conn.execute(
                    "UPDATE playlists SET name = ?, repeat_mode = ?, mute = ?, updated_at = ?, "
                    "transition_enabled = ?, transition_media_id = ?, new_item_transition = ?, shuffle = ? WHERE id = ?",
                    (name, repeat_mode, mute, db.now(), t_enabled, t_media, new_item_t, shuffle, row["id"]))
                if clean_items is not None:
                    conn.execute("DELETE FROM playlist_items WHERE playlist_id = ?", (row["id"],))
                    conn.executemany(
                        "INSERT INTO playlist_items (playlist_id, media_id, position, duration, scale_mode, "
                        "transition_mode, transition_media_id, logo_media_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [(row["id"], mid, pos, dur, sc, tm, tmid, logo)
                         for pos, (mid, dur, sc, tm, tmid, logo) in enumerate(clean_items)])
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
        return jsonify(ok=True, warnings=playlist_warnings(conn, cfg, row))

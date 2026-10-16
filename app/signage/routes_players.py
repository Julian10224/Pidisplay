"""API: players/schermen beheren en de endpoints die players zelf aanroepen."""

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid

from flask import g, jsonify, request, send_file

from . import __version__, db, media
from .app import ApiError, body, get_db, hash_token, refresh_standby, state
from .routes_playlists import get_playlist_row, playlist_items

log = logging.getLogger("signage.players")

ONLINE_AFTER = 45        # seconden zonder heartbeat => offline
LAST_SEEN_WRITE = 300    # schrijf last_seen hooguit eens per 5 minuten (SD-kaart sparen)
CONTROL_ACTIONS = ("next", "prev", "goto")
CONTROL_TTL = 15         # een opdracht die zo lang onderweg is, wordt niet meer uitgevoerd
CONTROL_WAIT = 25        # maximale wachttijd van de player bij het ophalen van opdrachten


def effective_playlist_id(conn, player_row, settings=None):
    settings = settings or db.get_settings(conn)
    if player_row["playlist_id"] is not None:
        return player_row["playlist_id"]
    return settings.get("default_playlist_id")


def active_playlist_ids(conn):
    settings = db.get_settings(conn)
    return {effective_playlist_id(conn, r, settings) for r in conn.execute("SELECT * FROM players")} - {None}


def _file_entry(cfg, row):
    paths = media.media_paths(cfg, row)
    return {
        "path": paths["playback"], "url": f"/api/player/v1/media/{row['id']}",
        "sha256": row["playback_sha256"], "size": row["playback_size"],
        "ext": os.path.splitext(paths["playback"])[1].lower(),
    }


def _html_entry(cfg, row):
    if row["playback_dir"] == "builtin":
        return {"source": "builtin", "page": row["playback_name"], "file": row["html_entry"],
                "width": row["html_width"], "height": row["html_height"]}
    paths = media.media_paths(cfg, row)
    return {"source": "upload", "entry": row["html_entry"] or "index.html", "path": paths["playback"],
            "url": f"/api/player/v1/media/{row['id']}", "sha256": row["playback_sha256"],
            "size": row["playback_size"], "ext": row["ext"],
            "width": row["html_width"], "height": row["html_height"]}


def _logo_entry(conn, cfg, logo_id, cache):
    if not logo_id:
        return None
    if logo_id not in cache:
        lrow = conn.execute("SELECT * FROM media WHERE id = ? AND kind = 'image'", (logo_id,)).fetchone()
        cache[logo_id] = ({"media_id": logo_id, "name": lrow["name"], **_file_entry(cfg, lrow)}
                          if lrow is not None and media.playback_available(cfg, lrow) else None)
    return cache[logo_id]


def _items_payload(conn, cfg, pl_row, settings, transitions, logos=None):
    from .routes_playlists import resolve_transition_id
    out = []
    for it in playlist_items(conn, pl_row["id"]):
        if not media.playback_available(cfg, it):
            continue  # ontbrekende bestanden worden nooit naar de player gestuurd
        entry = {
            "media_id": it["id"], "item_id": it["item_id"], "name": it["name"], "kind": it["kind"],
            "scale": it["item_scale_mode"] or settings.get("scale_mode", "cover"),
            "duration": None if it["kind"] == "video" else (it["item_duration"] or settings["default_image_duration"]),
            "video_duration": it["duration"] if it["kind"] == "video" else None,
            "transition": None,
            "logo": _logo_entry(conn, cfg, it["item_logo_media_id"], logos if logos is not None else {}),
        }
        if it["kind"] == "html":
            entry["html"] = _html_entry(cfg, it)
        else:
            entry.update(_file_entry(cfg, it))
        tid = resolve_transition_id(pl_row, it)
        if tid:
            if tid not in transitions:
                trow = conn.execute("SELECT * FROM media WHERE id = ? AND kind = 'video'", (tid,)).fetchone()
                if trow is not None and media.playback_available(cfg, trow):
                    transitions[tid] = {"media_id": tid, "name": trow["name"], "kind": "video",
                                        "video_duration": trow["duration"], **_file_entry(cfg, trow)}
                else:
                    transitions[tid] = None   # ontbreekt: overgang wordt overgeslagen
            if transitions[tid]:
                entry["transition"] = tid
        out.append(entry)
    return out


def build_player_config(conn, cfg, player_row):
    st = state()
    settings = db.get_settings(conn)
    pid = effective_playlist_id(conn, player_row, settings)
    playlist = None
    items = []
    fallback = []
    transitions = {}
    if pid is not None:
        row = conn.execute("SELECT * FROM playlists WHERE id = ?", (pid,)).fetchone()
        if row is not None:
            playlist = {"id": row["id"], "name": row["name"], "repeat_mode": row["repeat_mode"],
                        "mute": bool(row["mute"]), "shuffle": bool(row["shuffle"])}
            logos = {}
            items = _items_payload(conn, cfg, row, settings, transitions, logos)
            default_id = settings.get("default_playlist_id")
            if row["repeat_mode"] == "once_then_default" and default_id and default_id != row["id"]:
                drow = conn.execute("SELECT * FROM playlists WHERE id = ?", (default_id,)).fetchone()
                if drow is not None:
                    fallback = _items_payload(conn, cfg, drow, settings, transitions, logos)
                    playlist["fallback_shuffle"] = bool(drow["shuffle"])
    if st.standby_sha is None or not os.path.exists(cfg.standby_path):
        refresh_standby(st, force=True)
    payload = {
        "player": {"id": player_row["id"], "name": player_row["name"], "rotation": player_row["rotation"]},
        "system_name": settings["system_name"],
        "playlist": playlist,
        "items": items,
        "fallback_items": fallback,
        "transitions": {k: v for k, v in transitions.items() if v},
        "scale_default": settings.get("scale_mode", "cover"),
        "volume": int(settings.get("volume", 80)),
        "standby": {"path": cfg.standby_path, "url": "/api/player/v1/standby", "sha256": st.standby_sha},
    }
    payload["revision"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
    return payload


def _player_from_token(conn):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise ApiError(401, "Player-token ontbreekt.")
    token_hash = hash_token(auth[7:].strip())
    for row in conn.execute("SELECT * FROM players"):
        if hmac.compare_digest(row["token_hash"], token_hash):
            return row
    raise ApiError(401, "Onbekend player-token.")


def live_status(st, player_id):
    with st.lock:
        rec = st.live.get(player_id)
    if not rec:
        return None, False
    return rec["status"], (time.time() - rec["received"]) < ONLINE_AFTER


def player_to_json(conn, row, settings, st):
    status, online = live_status(st, row["id"])
    pid = effective_playlist_id(conn, row, settings)
    pl = conn.execute("SELECT id, name FROM playlists WHERE id = ?", (pid,)).fetchone() if pid else None
    last = None
    with st.lock:
        rec = st.live.get(row["id"])
        if rec:
            last = rec["received"]
    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"], "display_name": row["display_name"],
        "rotation": row["rotation"], "playlist_id": row["playlist_id"],
        "follows_default": row["playlist_id"] is None,
        "effective_playlist": dict(pl) if pl else None,
        "online": online, "last_contact": last or row["last_seen"], "status": status,
        "enabled": row["token_hash"] != "!disabled",
    }


def register(app):

    # -- beheer ---------------------------------------------------------------
    @app.get("/api/players")
    def players_list():
        st = state()
        conn = get_db()
        settings = db.get_settings(conn)
        rows = conn.execute("SELECT * FROM players ORDER BY kind = 'remote', name").fetchall()
        return jsonify(players=[player_to_json(conn, r, settings, st) for r in rows])

    @app.post("/api/players")
    def players_create():
        name = re.sub(r"[\x00-\x1f\x7f]", "", str(body().get("name", ""))).strip()[:60]
        if not name:
            raise ApiError(400, "Geef de player een naam.")
        token = secrets.token_urlsafe(32)
        pid = uuid.uuid4().hex[:12]
        get_db().execute(
            "INSERT INTO players (id, name, kind, token_hash, created_at) VALUES (?, ?, 'remote', ?, ?)",
            (pid, name, hash_token(token), db.now()))
        log.info("Externe player '%s' geregistreerd.", name)
        return jsonify(id=pid, token=token), 201

    def _get_player(conn, player_id):
        row = conn.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
        if row is None:
            raise ApiError(404, "Player niet gevonden.")
        return row

    @app.patch("/api/players/<player_id>")
    def players_update(player_id):
        conn = get_db()
        row = _get_player(conn, player_id)
        data = body()
        name = re.sub(r"[\x00-\x1f\x7f]", "", str(data.get("name", row["name"]))).strip()[:60] or row["name"]
        display = re.sub(r"[\x00-\x1f\x7f]", "", str(data.get("display_name", row["display_name"]))).strip()[:60]
        rotation = data.get("rotation", row["rotation"])
        try:
            rotation = int(rotation)
        except (TypeError, ValueError):
            rotation = -1
        if rotation not in (0, 90, 180, 270):
            raise ApiError(400, "Rotatie moet 0, 90, 180 of 270 zijn.")
        playlist_id = row["playlist_id"]
        if "playlist_id" in data:
            if data["playlist_id"] in (None, "", "default"):
                playlist_id = None
            else:
                pl = get_playlist_row(conn, data["playlist_id"])
                from .routes_playlists import playlist_problems
                problems = playlist_problems(conn, state().cfg, pl["id"])
                if problems:
                    raise ApiError(422, "Playlist kan niet worden geactiveerd.", problems=problems)
                playlist_id = pl["id"]
        conn.execute("UPDATE players SET name = ?, display_name = ?, rotation = ?, playlist_id = ? WHERE id = ?",
                     (name, display or "HDMI-scherm", rotation, playlist_id, row["id"]))
        st = state()
        return jsonify(player=player_to_json(conn, _get_player(conn, row["id"]), db.get_settings(conn), st))

    @app.delete("/api/players/<player_id>")
    def players_delete(player_id):
        conn = get_db()
        row = _get_player(conn, player_id)
        if row["kind"] == "local":
            raise ApiError(400, "De lokale player kan niet worden verwijderd.")
        conn.execute("DELETE FROM players WHERE id = ?", (row["id"],))
        with state().lock:
            state().live.pop(row["id"], None)
        with state().control_cv:
            state().control.pop(row["id"], None)
        return jsonify(ok=True)

    @app.post("/api/players/<player_id>/token")
    def players_new_token(player_id):
        conn = get_db()
        row = _get_player(conn, player_id)
        if row["kind"] == "local":
            raise ApiError(400, "Het token van de lokale player staat in /etc/signage/player.ini.")
        token = secrets.token_urlsafe(32)
        conn.execute("UPDATE players SET token_hash = ? WHERE id = ?", (hash_token(token), row["id"]))
        return jsonify(token=token)

    @app.post("/api/players/<player_id>/control")
    def players_control(player_id):
        """Live bediening: het scherm direct naar het vorige/volgende item of naar een
        bepaald item laten gaan. De player haalt de opdracht binnen een seconde op."""
        conn = get_db()
        row = _get_player(conn, player_id)
        data = body()
        action = data.get("action")
        if action not in CONTROL_ACTIONS:
            raise ApiError(400, "Onbekende opdracht.")
        cmd = {"action": action, "transition": bool(data.get("transition"))}
        if action == "goto":
            item_id = data.get("item_id")
            if not isinstance(item_id, int) or isinstance(item_id, bool):
                raise ApiError(400, "Geef aan naar welk item het scherm moet gaan.")
            pid = effective_playlist_id(conn, row)
            if pid is None or conn.execute("SELECT 1 FROM playlist_items WHERE id = ? AND playlist_id = ?",
                                           (item_id, pid)).fetchone() is None:
                raise ApiError(409, "Dit item staat niet (meer) in de playlist die op dit scherm speelt.")
            cmd["item_id"] = item_id
        st = state()
        status, online = live_status(st, row["id"])
        if not online:
            raise ApiError(409, "Dit scherm is niet online; de opdracht is niet verstuurd.")
        if not (status or {}).get("control"):
            raise ApiError(409, "Deze player ondersteunt live bediening nog niet. Werk de player bij naar 1.3.")
        now = time.time()
        with st.control_cv:
            box = st.control.setdefault(row["id"], {"seq": 0, "cmds": []})
            box["seq"] += 1
            cmd.update(seq=box["seq"], at=now)
            box["cmds"] = [c for c in box["cmds"] if now - c["at"] < CONTROL_TTL][-19:] + [cmd]
            st.control_cv.notify_all()
        return jsonify(ok=True, seq=cmd["seq"])

    # -- endpoints voor players -------------------------------------------------
    @app.get("/api/player/v1/control")
    def player_control():
        """Opdrachten voor de live bediening ophalen (long polling).

        De player stuurt het laatst ontvangen volgnummer mee ('after') en het 'epoch' van de
        server. Zonder of met een verouderd epoch krijgt hij alleen het huidige volgnummer
        terug, zodat oude opdrachten nooit (opnieuw) worden uitgevoerd. Er wachten nooit meer
        verzoeken tegelijk dan de server aankan; daarboven antwoordt de server direct."""
        st = state()
        conn = get_db()
        pid = _player_from_token(conn)["id"]
        g.pop("db").close()   # geen databaseverbinding vasthouden tijdens het wachten
        epoch = request.args.get("epoch", "")
        try:
            after = int(request.args.get("after", ""))
        except ValueError:
            after = None
        try:
            wait = max(0.0, min(float(request.args.get("wait", CONTROL_WAIT)), CONTROL_WAIT))
        except ValueError:
            wait = CONTROL_WAIT

        def seq_now():
            return (st.control.get(pid) or {}).get("seq", 0)

        retry = 0
        with st.control_cv:
            seq = seq_now()
            if epoch != st.control_epoch or after is None or after > seq:
                return jsonify(epoch=st.control_epoch, seq=seq, commands=[], retry=0)
            if wait and seq == after:
                if st.control_waiters < max(1, st.cfg.threads - 3):
                    st.control_waiters += 1
                    try:
                        st.control_cv.wait_for(lambda: seq_now() > after, timeout=wait)
                    finally:
                        st.control_waiters -= 1
                else:
                    retry = 2   # druk: de player vraagt over 2 s opnieuw
            now = time.time()
            box = st.control.get(pid) or {"seq": 0, "cmds": []}
            cmds = [{"seq": c["seq"], "action": c["action"], "transition": c["transition"],
                     "item_id": c.get("item_id"), "age": round(now - c["at"], 2)}
                    for c in box["cmds"] if c["seq"] > after and now - c["at"] < CONTROL_TTL]
            return jsonify(epoch=st.control_epoch, seq=box["seq"], commands=cmds, retry=retry)

    @app.get("/api/player/v1/config")
    def player_config():
        cfg = state().cfg
        conn = get_db()
        row = _player_from_token(conn)
        payload = build_player_config(conn, cfg, row)
        etag = '"%s"' % payload["revision"]
        if request.headers.get("If-None-Match") == etag:
            resp = app.response_class(status=304)
            resp.headers["ETag"] = etag
            return resp
        resp = jsonify(payload)
        resp.headers["ETag"] = etag
        return resp

    @app.post("/api/player/v1/heartbeat")
    def player_heartbeat():
        st = state()
        conn = get_db()
        row = _player_from_token(conn)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ApiError(400, "Verwacht JSON.")
        allowed = ("state", "current", "playlist_id", "revision", "mpv_restarts", "errors", "display",
                   "version", "uptime", "message", "renderer", "phase", "video_stats", "viewport", "hwdec",
                   "renderer_note", "control")
        status = {k: data.get(k) for k in allowed if k in data}
        if "control" in status:
            c = status["control"] if isinstance(status["control"], dict) else {}
            last = c.get("last") if isinstance(c.get("last"), dict) else None
            status["control"] = {"v": c.get("v"), "last": {k: (str(last.get(k))[:200] if last.get(k) is not None else None)
                                                           for k in ("seq", "action", "result")} if last else None}
        if isinstance(status.get("errors"), list):
            status["errors"] = [str(e)[:300] for e in status["errors"][-10:]]
        now = time.time()
        with st.lock:
            was = st.live.get(row["id"])
            st.live[row["id"]] = {"status": status, "received": now}
            last_written = st.last_seen_written.get(row["id"], 0)
            write = (now - last_written > LAST_SEEN_WRITE) or not was or (now - was["received"] > ONLINE_AFTER)
            if write:
                st.last_seen_written[row["id"]] = now
        if write:
            conn.execute("UPDATE players SET last_seen = ? WHERE id = ?", (now, row["id"]))
        return jsonify(ok=True, server_version=__version__)

    @app.get("/api/player/v1/media/<media_id>")
    def player_media(media_id):
        cfg = state().cfg
        conn = get_db()
        _player_from_token(conn)
        if not re.match(r"^[0-9a-f]{32}$", media_id):
            raise ApiError(404, "Niet gevonden.")
        row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
        if row is None or row["playback_dir"] == "builtin":
            raise ApiError(404, "Niet gevonden.")
        paths = media.media_paths(cfg, row)
        # Voor HTML-projecten krijgt een externe player het originele .html/.zip-bestand.
        path = paths["file"] if row["playback_dir"] == "html" else paths["playback"]
        if not path or not os.path.isfile(path):
            raise ApiError(404, "Bestand ontbreekt.")
        return send_file(path, mimetype="application/octet-stream", conditional=True, max_age=0)

    @app.get("/api/player/v1/standby")
    def player_standby():
        cfg = state().cfg
        _player_from_token(get_db())
        return send_file(cfg.standby_path, mimetype="image/png", max_age=0)

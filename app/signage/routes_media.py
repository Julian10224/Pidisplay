"""API: mediabeheer (upload, lijst, hernoemen, verwijderen, bestanden serveren)."""

import hashlib
import logging
import os
import re
import shutil
import threading
import uuid
from urllib.parse import unquote

from flask import jsonify, request, send_file

from . import db, media
from .app import ApiError, body, get_db, state

log = logging.getLogger("signage.media")

ID_RE = re.compile(r"^[0-9a-f]{32}$")
_processing = threading.Semaphore(1)   # één zware verwerking tegelijk (1 GB RAM)


def media_row_to_json(cfg, row, usage=None):
    paths = media.media_paths(cfg, row)
    out = {k: row[k] for k in ("id", "name", "original_filename", "kind", "format", "ext", "size",
                                "width", "height", "duration", "codec", "status", "warning", "created_at")}
    out["missing"] = not os.path.exists(paths["playback"])
    out["has_thumb"] = bool(paths["thumb"] and os.path.exists(paths["thumb"]))
    if usage is not None:
        out["playlists"] = usage.get(row["id"], [])
    return out


def usage_map(conn):
    usage = {}
    for r in conn.execute(
            "SELECT DISTINCT pi.media_id, p.id, p.name FROM playlist_items pi "
            "JOIN playlists p ON p.id = pi.playlist_id ORDER BY p.name"):
        usage.setdefault(r["media_id"], []).append({"id": r["id"], "name": r["name"]})
    return usage


def get_media_row(conn, media_id):
    if not ID_RE.match(media_id or ""):
        raise ApiError(404, "Media niet gevonden.")
    row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Media niet gevonden.")
    return row


def register(app):

    @app.get("/api/media")
    def media_list():
        cfg = state().cfg
        conn = get_db()
        usage = usage_map(conn)
        rows = conn.execute("SELECT * FROM media ORDER BY created_at DESC").fetchall()
        du = shutil.disk_usage(cfg.media_dir)
        return jsonify(
            media=[media_row_to_json(cfg, r, usage) for r in rows],
            limits={"max_upload_bytes": cfg.max_upload_bytes, "free_bytes": du.free,
                    "min_free_bytes": cfg.min_free_bytes,
                    "allowed": sorted(media.ALLOWED_EXTS)})

    @app.post("/api/media/upload")
    def media_upload():
        cfg = state().cfg
        raw_name = request.headers.get("X-Filename", "")
        original = unquote(raw_name)[:255]
        ext = media.file_ext(original)
        if not original or ext not in media.ALLOWED_EXTS:
            raise ApiError(415, f"Bestandstype '{ext or 'onbekend'}' wordt niet ondersteund. "
                                "Toegestaan: PNG, JPEG, WebP, MP4, M4V, MOV, MKV, WebM.")
        length = request.content_length
        if length is None:
            raise ApiError(411, "Content-Length ontbreekt.")
        if length == 0:
            raise ApiError(400, "Het bestand is leeg.")
        if length > cfg.max_upload_bytes:
            raise ApiError(413, f"Bestand is groter dan de maximale uploadgrootte "
                                f"({cfg.max_upload_bytes // (1024 * 1024)} MB).")
        free = shutil.disk_usage(cfg.media_dir).free
        if free - int(length * 1.1) < cfg.min_free_bytes:
            raise ApiError(507, "Onvoldoende vrije opslagruimte voor dit bestand.")

        media_id = uuid.uuid4().hex
        part = os.path.join(cfg.incoming_dir, media_id + ".part")
        h = hashlib.sha256()
        received = 0
        try:
            with open(part, "wb") as out:
                stream = request.stream
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > length:
                        raise ApiError(400, "Upload groter dan aangekondigd.")
                    h.update(chunk)
                    out.write(chunk)
            if received != length:
                raise ApiError(400, "Upload onvolledig ontvangen; probeer het opnieuw.")
            sha = h.hexdigest()

            conn = get_db()
            dup = conn.execute("SELECT * FROM media WHERE sha256 = ?", (sha,)).fetchone()
            if dup is not None and os.path.exists(media.media_paths(cfg, dup)["playback"]):
                os.unlink(part)
                return jsonify(media=media_row_to_json(cfg, dup, usage_map(conn)), duplicate=True)

            with _processing:
                try:
                    info = media.process_incoming(cfg, part, original, media_id)
                except media.MediaError as exc:
                    raise ApiError(422, str(exc))
            created = info.pop("_created")
            try:
                conn.execute(
                    "INSERT INTO media (id, name, original_filename, kind, format, ext, size, sha256, width, "
                    "height, duration, codec, status, warning, file_name, playback_name, playback_dir, "
                    "playback_sha256, playback_size, thumb_name, created_at) VALUES "
                    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (media_id, media.safe_display_name(original), os.path.basename(original), info["kind"],
                     info["format"], info["ext"], received, sha, info["width"], info["height"],
                     info["duration"], info["codec"], info["status"], info["warning"], info["file_name"],
                     info["playback_name"], info["playback_dir"], info["playback_sha256"],
                     info["playback_size"], info["thumb_name"], db.now()))
            except Exception:
                for p in created:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
                raise
            log.info("Media geüpload: %s (%s, %d bytes)", original, info["kind"], received)
            row = get_media_row(conn, media_id)
            return jsonify(media=media_row_to_json(cfg, row, {}), duplicate=False), 201
        finally:
            if os.path.exists(part):
                try:
                    os.unlink(part)
                except OSError:
                    pass

    @app.patch("/api/media/<media_id>")
    def media_rename(media_id):
        conn = get_db()
        get_media_row(conn, media_id)
        name = re.sub(r"[\x00-\x1f\x7f]", "", str(body().get("name", ""))).strip()[:120]
        if not name:
            raise ApiError(400, "Naam mag niet leeg zijn.")
        conn.execute("UPDATE media SET name = ? WHERE id = ?", (name, media_id))
        return jsonify(media=media_row_to_json(state().cfg, get_media_row(conn, media_id), usage_map(conn)))

    @app.delete("/api/media/<media_id>")
    def media_delete(media_id):
        cfg = state().cfg
        conn = get_db()
        row = get_media_row(conn, media_id)
        used = usage_map(conn).get(media_id, [])
        force = request.args.get("force") == "1"
        if used and not force:
            from .routes_players import active_playlist_ids
            active = active_playlist_ids(conn)
            raise ApiError(409, "Dit bestand wordt gebruikt in een of meer playlists.",
                           playlists=used, in_active=any(p["id"] in active for p in used))
        with db.Tx(conn):
            conn.execute("DELETE FROM playlist_items WHERE media_id = ?", (media_id,))
            for p in used:
                conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (db.now(), p["id"]))
            conn.execute("DELETE FROM media WHERE id = ?", (media_id,))
        paths = media.media_paths(cfg, row)
        for p in {paths["file"], paths["playback"], paths["thumb"]}:
            if p:
                try:
                    os.unlink(p)
                except FileNotFoundError:
                    pass
        log.info("Media verwijderd: %s", row["name"])
        return jsonify(ok=True, removed_from=used)

    def _serve(media_id, which, as_attachment=False):
        cfg = state().cfg
        row = get_media_row(get_db(), media_id)
        paths = media.media_paths(cfg, row)
        path = paths[which]
        if not path or not os.path.exists(path):
            raise ApiError(404, "Bestand ontbreekt op de opslag.")
        ext = os.path.splitext(path)[1].lower()
        mimetype = media.MIME_TYPES.get(ext, "application/octet-stream")
        resp = send_file(path, mimetype=mimetype, conditional=True, as_attachment=as_attachment,
                         download_name=(row["name"] + row["ext"]) if as_attachment else None,
                         max_age=0 if which != "thumb" else 3600)
        return resp

    @app.get("/api/media/<media_id>/thumb")
    def media_thumb(media_id):
        return _serve(media_id, "thumb")

    @app.get("/api/media/<media_id>/playback")
    def media_playback(media_id):
        return _serve(media_id, "playback")

    @app.get("/api/media/<media_id>/download")
    def media_download(media_id):
        return _serve(media_id, "file", as_attachment=True)

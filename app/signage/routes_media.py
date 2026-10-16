"""API: mediabeheer (upload, lijst, hernoemen, verwijderen, bestanden serveren).

Sinds 1.1 ook HTML: geüploade HTML-bestanden/ZIP-projecten en ingebouwde lokale pagina's
(whitelist in htmlpkg.BUILTIN_PAGES).
"""

import hashlib
import hmac
import logging
import os
import re
import shutil
import threading
import uuid
from urllib.parse import unquote

from flask import current_app, jsonify, request, send_file, send_from_directory

from . import db, htmlpkg, media
from .app import ApiError, body, get_db, state

log = logging.getLogger("signage.media")

ID_RE = re.compile(r"^[0-9a-f]{32}$")
_processing = threading.Semaphore(1)   # één zware verwerking tegelijk (1 GB RAM)
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def preview_token(media_id):
    key = current_app.secret_key
    return hmac.new(key, ("preview:" + media_id).encode(), hashlib.sha256).hexdigest()[:32]


def media_row_to_json(cfg, row, usage=None):
    paths = media.media_paths(cfg, row)
    out = {k: row[k] for k in ("id", "name", "original_filename", "kind", "format", "ext", "size",
                                "width", "height", "duration", "codec", "status", "warning", "created_at")}
    out["missing"] = not media.playback_available(cfg, row)
    out["has_thumb"] = bool(paths["thumb"] and os.path.exists(paths["thumb"]))
    out["storage_bytes"] = storage_bytes(paths)
    out["transparent"] = row["kind"] == "image" and row["playback_name"].endswith(".png")
    if row["kind"] == "html":
        out["html_source"] = "builtin" if row["playback_dir"] == "builtin" else "upload"
        out["html_entry"] = row["html_entry"]
        out["html_width"] = row["html_width"]
        out["html_height"] = row["html_height"]
        if row["playback_dir"] == "builtin":
            page = htmlpkg.BUILTIN_PAGES.get(row["playback_name"], {})
            out["builtin_page"] = row["playback_name"]
            out["preview_url"] = f"/display/{page.get('file', '')}"
        else:
            out["preview_url"] = f"/preview/{row['id']}/{preview_token(row['id'])}/{row['html_entry']}"
    if row["kind"] == "video":
        try:
            out["optimize"] = state().optimizer.info(row["id"])
        except (RuntimeError, AttributeError, KeyError):
            out["optimize"] = None
        out["optimized"] = row["playback_dir"] == "renditions"
    if usage is not None:
        out["playlists"] = usage.get(row["id"], [])
    return out


def _path_size(p):
    if not p:
        return 0
    try:
        if os.path.isdir(p):
            total = 0
            for root, _d, files in os.walk(p):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return total
        return os.path.getsize(p)
    except OSError:
        return 0


def storage_bytes(paths):
    """Ruimte op de SD-kaart: origineel + afspeelversie/HTML-map + miniatuur."""
    return sum(_path_size(p) for p in {paths["file"], paths["playback"], paths["thumb"]} if p)


def usage_map(conn):
    """media_id -> lijst van playlists waarin het als item of als overgang voorkomt."""
    usage = {}

    def add(mid, pid, name, role):
        lst = usage.setdefault(mid, [])
        for e in lst:
            if e["id"] == pid:
                if role not in e["roles"]:
                    e["roles"].append(role)
                return
        lst.append({"id": pid, "name": name, "roles": [role]})

    for r in conn.execute("SELECT DISTINCT pi.media_id, p.id, p.name FROM playlist_items pi "
                          "JOIN playlists p ON p.id = pi.playlist_id ORDER BY p.name"):
        add(r["media_id"], r["id"], r["name"], "item")
    for r in conn.execute("SELECT DISTINCT pi.transition_media_id AS mid, p.id, p.name FROM playlist_items pi "
                          "JOIN playlists p ON p.id = pi.playlist_id WHERE pi.transition_media_id IS NOT NULL "
                          "UNION SELECT transition_media_id, id, name FROM playlists "
                          "WHERE transition_media_id IS NOT NULL"):
        add(r["mid"], r["id"], r["name"], "transition")
    for r in conn.execute("SELECT DISTINCT pi.logo_media_id AS mid, p.id, p.name FROM playlist_items pi "
                          "JOIN playlists p ON p.id = pi.playlist_id WHERE pi.logo_media_id IS NOT NULL"):
        add(r["mid"], r["id"], r["name"], "logo")
    return usage


def get_media_row(conn, media_id):
    if not ID_RE.match(media_id or ""):
        raise ApiError(404, "Media niet gevonden.")
    row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
    if row is None:
        raise ApiError(404, "Media niet gevonden.")
    return row


def _remove_paths(paths):
    for p in paths:
        if not p:
            continue
        try:
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.unlink(p)
        except FileNotFoundError:
            pass


INSERT_SQL = (
    "INSERT INTO media (id, name, original_filename, kind, format, ext, size, sha256, width, "
    "height, duration, codec, status, warning, file_name, playback_name, playback_dir, "
    "playback_sha256, playback_size, thumb_name, created_at, html_entry) VALUES "
    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)")


def register(app):

    @app.get("/api/media")
    def media_list():
        cfg = state().cfg
        conn = get_db()
        usage = usage_map(conn)
        rows = conn.execute("SELECT * FROM media ORDER BY created_at DESC").fetchall()
        du = shutil.disk_usage(cfg.media_dir)
        items = [media_row_to_json(cfg, r, usage) for r in rows]
        backups = sum(_path_size(os.path.join(cfg.backups_dir, f)) for f in os.listdir(cfg.backups_dir))
        summary = {"media_bytes": sum(m["storage_bytes"] for m in items), "backups_bytes": backups,
                   "disk_total": du.total, "disk_free": du.free,
                   "counts": {k: sum(1 for m in items if m["kind"] == k) for k in ("image", "video", "html")},
                   "unused": sum(1 for m in items if not m.get("playlists"))}
        return jsonify(
            media=items, summary=summary,
            limits={"max_upload_bytes": cfg.max_upload_bytes, "free_bytes": du.free,
                    "min_free_bytes": cfg.min_free_bytes,
                    "allowed": sorted(media.ALLOWED_EXTS)},
            builtin_pages=[{"id": k, **v} for k, v in htmlpkg.BUILTIN_PAGES.items()])

    @app.post("/api/media/upload")
    def media_upload():
        cfg = state().cfg
        raw_name = request.headers.get("X-Filename", "")
        original = unquote(raw_name)[:255]
        ext = media.file_ext(original)
        if not original or ext not in media.ALLOWED_EXTS:
            raise ApiError(415, f"Bestandstype '{ext or 'onbekend'}' wordt niet ondersteund. "
                                "Toegestaan: PNG, JPEG, WebP, MP4, M4V, MOV, MKV, WebM, HTML en ZIP (HTML-project).")
        length = request.content_length
        if length is None:
            raise ApiError(411, "Content-Length ontbreekt.")
        if length == 0:
            raise ApiError(400, "Het bestand is leeg.")
        if length > cfg.max_upload_bytes:
            raise ApiError(413, f"Bestand is groter dan de maximale uploadgrootte "
                                f"({cfg.max_upload_bytes // (1024 * 1024)} MB).")
        free = shutil.disk_usage(cfg.media_dir).free
        factor = 3 if ext == ".zip" else 1.1   # ZIP wordt ook uitgepakt
        if free - int(length * factor) < cfg.min_free_bytes:
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
            if dup is not None and media.playback_available(cfg, dup):
                os.unlink(part)
                return jsonify(media=media_row_to_json(cfg, dup, usage_map(conn)), duplicate=True)

            with _processing:
                try:
                    info = media.process_incoming(cfg, part, original, media_id)
                except media.MediaError as exc:
                    raise ApiError(422, str(exc))
            created = info.pop("_created")
            try:
                conn.execute(INSERT_SQL, (
                    media_id, media.safe_display_name(original), os.path.basename(original.replace("\\", "/")),
                    info["kind"], info["format"], info["ext"], received, sha, info["width"], info["height"],
                    info["duration"], info["codec"], info["status"], info["warning"], info["file_name"],
                    info["playback_name"], info["playback_dir"], info["playback_sha256"],
                    info["playback_size"], info["thumb_name"], db.now(), info.get("html_entry")))
            except Exception:
                _remove_paths(created)
                raise
            log.info("Media geüpload: %s (%s, %d bytes)", original, info["kind"], received)
            if info["kind"] == "video":
                state().optimizer.enqueue(media_id)   # zo nodig een afspeelversie maken
            row = get_media_row(conn, media_id)
            return jsonify(media=media_row_to_json(cfg, row, {}), duplicate=False), 201
        finally:
            if os.path.exists(part):
                try:
                    os.unlink(part)
                except OSError:
                    pass

    @app.post("/api/media/<media_id>/optimize")
    def media_optimize(media_id):
        """Afspeelversie (opnieuw) laten maken, bijvoorbeeld na een mislukte poging."""
        conn = get_db()
        row = get_media_row(conn, media_id)
        if row["kind"] != "video":
            raise ApiError(400, "Alleen video's kunnen worden omgezet.")
        if row["playback_dir"] != "files":
            raise ApiError(409, "Deze video heeft al een afspeelversie voor de Pi.")
        state().optimizer.enqueue(media_id, force=True)
        return jsonify(media=media_row_to_json(state().cfg, row, usage_map(conn)))

    @app.post("/api/media/builtin")
    def media_add_builtin():
        page = str(body().get("page", ""))
        spec = htmlpkg.BUILTIN_PAGES.get(page)
        if spec is None:
            raise ApiError(400, "Onbekende lokale pagina.")
        media_id = uuid.uuid4().hex
        conn = get_db()
        conn.execute(INSERT_SQL, (
            media_id, spec["name"], spec["file"], "html", "builtin", "", 0, f"builtin-{page}-{media_id}",
            None, None, None, None, "ok", None, "", page, "builtin", f"builtin-{page}", 0, None, db.now(),
            spec["file"]))
        return jsonify(media=media_row_to_json(state().cfg, get_media_row(conn, media_id), {})), 201

    @app.patch("/api/media/<media_id>")
    def media_update(media_id):
        conn = get_db()
        row = get_media_row(conn, media_id)
        data = body()
        name = row["name"]
        if "name" in data:
            name = re.sub(r"[\x00-\x1f\x7f]", "", str(data.get("name", ""))).strip()[:120]
            if not name:
                raise ApiError(400, "Naam mag niet leeg zijn.")
        hw, hh = row["html_width"], row["html_height"]
        if "html_width" in data or "html_height" in data:
            if row["kind"] != "html":
                raise ApiError(400, "Een ontwerpformaat kan alleen bij HTML worden ingesteld.")
            w, h = data.get("html_width"), data.get("html_height")
            if w in (None, "", 0) or h in (None, "", 0):
                hw = hh = None
            else:
                try:
                    hw, hh = int(w), int(h)
                except (TypeError, ValueError):
                    raise ApiError(400, "Ongeldig ontwerpformaat.")
                if not (100 <= hw <= 7680 and 100 <= hh <= 7680):
                    raise ApiError(400, "Ontwerpformaat moet tussen 100 en 7680 pixels liggen.")
        conn.execute("UPDATE media SET name = ?, html_width = ?, html_height = ? WHERE id = ?",
                     (name, hw, hh, media_id))
        return jsonify(media=media_row_to_json(state().cfg, get_media_row(conn, media_id), usage_map(conn)))

    def _delete_rows(conn, cfg, rows, used_by_id):
        """Verwijder media-records en bestanden; verwijzingen worden eerst opgeruimd."""
        touched = {p["id"] for mid in used_by_id for p in used_by_id[mid]}
        with db.Tx(conn):
            for row in rows:
                mid = row["id"]
                conn.execute("DELETE FROM playlist_items WHERE media_id = ?", (mid,))
                conn.execute("UPDATE playlist_items SET transition_media_id = NULL, transition_mode = "
                             "CASE WHEN transition_mode = 'custom' THEN 'default' ELSE transition_mode END "
                             "WHERE transition_media_id = ?", (mid,))
                conn.execute("UPDATE playlists SET transition_media_id = NULL WHERE transition_media_id = ?", (mid,))
                conn.execute("UPDATE playlist_items SET logo_media_id = NULL WHERE logo_media_id = ?", (mid,))
                conn.execute("DELETE FROM media WHERE id = ?", (mid,))
            for pid in touched:
                conn.execute("UPDATE playlists SET updated_at = ? WHERE id = ?", (db.now(), pid))
        for row in rows:
            state().optimizer.forget(row["id"])
            paths = media.media_paths(cfg, row)
            _remove_paths({paths["file"], paths["playback"], paths["thumb"]})
            log.info("Media verwijderd: %s", row["name"])

    def _in_use_error(conn, used_list, message):
        from .routes_players import active_playlist_ids
        active = active_playlist_ids(conn)
        return ApiError(409, message, playlists=used_list, in_active=any(p["id"] in active for p in used_list))

    @app.delete("/api/media/<media_id>")
    def media_delete(media_id):
        cfg = state().cfg
        conn = get_db()
        row = get_media_row(conn, media_id)
        used = usage_map(conn).get(media_id, [])
        if used and request.args.get("force") != "1":
            raise _in_use_error(conn, used, "Dit bestand wordt gebruikt in een of meer playlists.")
        _delete_rows(conn, cfg, [row], {media_id: used})
        return jsonify(ok=True, removed_from=used)

    @app.post("/api/media/delete")
    def media_bulk_delete():
        """Meerdere bestanden tegelijk verwijderen (Media-overzicht)."""
        cfg = state().cfg
        conn = get_db()
        data = body()
        ids = data.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 1000:
            raise ApiError(400, "Geef een lijst met te verwijderen bestanden.")
        rows = [get_media_row(conn, str(i)) for i in dict.fromkeys(ids)]
        usage = usage_map(conn)
        used_by_id = {r["id"]: usage.get(r["id"], []) for r in rows}
        in_use = [{"id": r["id"], "name": r["name"], "playlists": used_by_id[r["id"]]} for r in rows if used_by_id[r["id"]]]
        if in_use and not data.get("force"):
            merged = {}
            for e in in_use:
                for p in e["playlists"]:
                    merged.setdefault(p["id"], p)
            err = _in_use_error(conn, list(merged.values()), f"{len(in_use)} van de {len(rows)} bestanden worden gebruikt.")
            err.extra["in_use"] = in_use
            raise err
        _delete_rows(conn, cfg, rows, used_by_id)
        return jsonify(ok=True, deleted=len(rows), removed_from=in_use)

    def _serve(media_id, which, as_attachment=False):
        cfg = state().cfg
        row = get_media_row(get_db(), media_id)
        paths = media.media_paths(cfg, row)
        path = paths[which]
        if not path or not os.path.isfile(path):
            raise ApiError(404, "Bestand ontbreekt op de opslag.")
        ext = os.path.splitext(path)[1].lower()
        mimetype = media.MIME_TYPES.get(ext, "application/octet-stream")
        if as_attachment:
            mimetype = "application/octet-stream" if row["kind"] == "html" else mimetype
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

    # -- HTML-voorvertoning ----------------------------------------------------
    # Geleverd buiten /api met een capability-token in het pad en een CSP-sandbox, zodat
    # geüploade scripts geen toegang hebben tot de beheeromgeving (eigen, ondoorzichtige origin).
    @app.get("/preview/<media_id>/<token>/<path:rel>")
    def html_preview(media_id, token, rel):
        if not ID_RE.match(media_id) or not hmac.compare_digest(token, preview_token(media_id)):
            raise ApiError(404, "Niet gevonden.")
        cfg = state().cfg
        row = get_db().execute("SELECT * FROM media WHERE id = ? AND kind = 'html'", (media_id,)).fetchone()
        if row is None or row["playback_dir"] != "html":
            raise ApiError(404, "Niet gevonden.")
        root = os.path.join(cfg.html_dir, row["playback_name"])
        full = htmlpkg.safe_join(root, rel)
        if not full or not os.path.isfile(full):
            raise ApiError(404, "Niet gevonden.")
        resp = send_file(full, conditional=True, max_age=0)
        resp.headers["Content-Security-Policy"] = "sandbox allow-scripts allow-forms allow-popups"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        return resp

    @app.get("/display/<path:fname>")
    def display_page(fname):
        allowed = {spec["file"] for spec in htmlpkg.BUILTIN_PAGES.values()} | {"display.css"}
        if fname not in allowed:
            raise ApiError(404, "Niet gevonden.")
        return send_from_directory(os.path.join(STATIC_DIR, "display"), fname, max_age=300)

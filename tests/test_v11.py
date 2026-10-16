"""Tests voor 1.1: migratie, HTML-content, overgangen en schaalmodi."""

import io
import os
import sqlite3
import stat
import zipfile

from PIL import Image

from conftest import login, make_config, upload

AUTH = {"Authorization": "Bearer local-test-token-0123456789"}
HERE = os.path.dirname(os.path.abspath(__file__))


def make_zip(entries, symlink=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


def up_bytes(client, data, name):
    from urllib.parse import quote
    return client.post("/api/media/upload", data=data,
                       headers={**client.h, "X-Filename": quote(name), "Content-Type": "application/octet-stream"})


# -- migratie 1.0.0 -> 1.1 ----------------------------------------------------------

def test_migration_from_v1(tmp_path, monkeypatch):
    ini, _ = make_config(tmp_path)
    from signage.config import Config
    cfg = Config(ini)
    cfg.ensure_dirs()
    # Database zoals versie 1.0.0 die achterliet, met een afbeelding en een playlist.
    conn = sqlite3.connect(cfg.db_path)
    conn.executescript(open(os.path.join(HERE, "v1_schema.sql")).read())
    conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    conn.execute("INSERT INTO settings VALUES ('volume', '55')")
    mid = "a" * 32
    Image.new("RGB", (1080, 1920), (200, 0, 0)).save(os.path.join(cfg.files_dir, mid + ".jpg"))
    Image.new("RGB", (607, 1080), (200, 0, 0)).save(os.path.join(cfg.renditions_dir, mid + ".jpg"))
    conn.execute(
        "INSERT INTO media VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mid, "staand", "staand.jpg", "image", "JPEG", ".jpg", 1000, "sha", 1080, 1920, None, "jpeg", "ok",
         None, mid + ".jpg", mid + ".jpg", "renditions", "oud", 10, None, 1.0))
    conn.execute("INSERT INTO playlists (id, name, created_at, updated_at) VALUES (1, 'Oud', 1, 1)")
    conn.execute("INSERT INTO playlist_items (playlist_id, media_id, position, duration) VALUES (1, ?, 0, 7)", (mid,))
    conn.execute("INSERT INTO settings VALUES ('default_playlist_id', '1')")
    conn.commit()
    conn.close()

    from signage.app import create_app
    app = create_app(cfg, start_background=False)
    conn = sqlite3.connect(cfg.db_path)
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == str(__import__('signage.db', fromlist=['x']).SCHEMA_VERSION)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    row = conn.execute("SELECT name, playback_sha256 FROM media").fetchone()
    assert row[0] == "staand" and row[1] != "oud", "afspeelversie opnieuw gemaakt"
    item = conn.execute("SELECT duration, scale_mode, transition_mode FROM playlist_items").fetchone()
    assert item == (7.0, None, "default")
    pl = conn.execute("SELECT transition_enabled, transition_media_id, new_item_transition FROM playlists").fetchone()
    assert pl == (0, None, "default")
    conn.close()
    with Image.open(os.path.join(cfg.renditions_dir, mid + ".jpg")) as img:
        assert img.size == (1080, 1920), "staande foto groot genoeg voor beeldvullend tonen"
    backups = os.listdir(cfg.backups_dir)
    assert any(b.startswith("signage-backup-pre-migration-") for b in backups)
    # Oude playlist werkt met standaardwaarden.
    c = app.test_client()
    r = c.get("/api/player/v1/config", headers=AUTH)
    assert r.status_code == 200
    it = r.json["items"][0]
    assert it["scale"] == "cover" and it["transition"] is None and it["duration"] == 7
    assert r.json["volume"] == 55
    # Tweede start migreert niet opnieuw.
    create_app(cfg, start_background=False)
    assert len([b for b in os.listdir(cfg.backups_dir) if "pre-migration" in b]) == 1


# -- HTML-content ----------------------------------------------------------------------

def test_html_single_file(client):
    r = up_bytes(client, b"<!doctype html><h1>Hallo</h1>", "aankondiging.html")
    assert r.status_code == 201, r.json
    m = r.json["media"]
    assert m["kind"] == "html" and m["html_source"] == "upload" and m["html_entry"] == "index.html"
    r2 = client.get(m["preview_url"])
    assert r2.status_code == 200 and b"Hallo" in r2.data
    assert "sandbox" in r2.headers["Content-Security-Policy"]
    bad = m["preview_url"].replace(m["preview_url"].split("/")[3], "0" * 32)
    assert client.get(bad).status_code == 404


def test_html_zip_with_assets_and_prefix(client):
    data = make_zip({
        "site/index.html": '<link rel="stylesheet" href="css/s.css"><img src="img/a.png">'
                           '<script src="https://cdn.example.com/x.js"></script>',
        "site/css/s.css": "body{margin:0}",
        "site/img/a.png": b"\x89PNG\r\n",
        "__MACOSX/site/._index.html": "x",
    })
    r = up_bytes(client, data, "project.zip")
    assert r.status_code == 201, r.json
    m = r.json["media"]
    assert m["status"] == "warning" and "cdn.example.com" in m["warning"]
    root = os.path.join(client.cfg.html_dir, m["id"])
    assert sorted(os.listdir(root)) == ["css", "img", "index.html"]
    base = m["preview_url"].rsplit("/", 1)[0]
    assert client.get(base + "/css/s.css").status_code == 200
    assert client.get(base + "/../../etc/passwd").status_code == 404
    assert client.get(base + "/%2e%2e/%2e%2e/secret_key").status_code == 404


def test_html_zip_rejections(client):
    cases = {
        "traversal": make_zip({"../evil.html": "x", "index.html": "x"}),
        "absolute": make_zip({"/etc/cron.d/x": "x", "index.html": "x"}),
        "symlink": make_zip({"index.html": "x"}, symlink="link"),
        "type": make_zip({"index.html": "x", "run.sh": "rm -rf /"}),
        "noindex": make_zip({"a.html": "x", "b.html": "y"}),
        "bomb": make_zip({"index.html": "x", "big.txt": "0" * (5 * 1024 * 1024)}),
        "notzip": b"dit is geen zip",
    }
    for name, data in cases.items():
        r = up_bytes(client, data, f"{name}.zip")
        assert r.status_code == 422, (name, r.json)
    assert os.listdir(client.cfg.html_dir) == []
    assert os.listdir(client.cfg.files_dir) == []
    assert os.listdir(client.cfg.incoming_dir) == []


def test_builtin_pages(client):
    lst = client.get("/api/media").json["builtin_pages"]
    assert {p["id"] for p in lst} == {"clock", "welcome"}
    r = client.post("/api/media/builtin", json={"page": "clock"}, headers=client.h)
    assert r.status_code == 201
    m = r.json["media"]
    assert m["kind"] == "html" and not m["missing"]
    assert client.get(m["preview_url"]).status_code == 200
    assert client.post("/api/media/builtin", json={"page": "../app"}, headers=client.h).status_code == 400
    assert client.get("/display/../app.py").status_code in (404, 400)
    assert client.get("/display/index.html").status_code == 404


def test_html_design_size(client):
    m = up_bytes(client, b"<p>x</p>", "x.html").json["media"]
    r = client.patch(f"/api/media/{m['id']}", json={"html_width": 1920, "html_height": 1080}, headers=client.h)
    assert r.json["media"]["html_width"] == 1920
    assert client.patch(f"/api/media/{m['id']}", json={"html_width": 5, "html_height": 5},
                        headers=client.h).status_code == 400


# -- overgangen en schaalmodi -------------------------------------------------------------

def _setup_playlist(client, media_files):
    img = upload(client, media_files["png"]).json["media"]["id"]
    vid = upload(client, media_files["mp4"]).json["media"]["id"]
    html = up_bytes(client, b"<p>pagina</p>", "pagina.html").json["media"]["id"]
    pid = client.post("/api/playlists", json={"name": "Mix"}, headers=client.h).json["id"]
    return img, vid, html, pid


def test_transitions_and_scale_in_player_config(client, media_files):
    img, vid, html, pid = _setup_playlist(client, media_files)
    r = client.put(f"/api/playlists/{pid}", json={
        "transition_enabled": True, "transition_media_id": vid, "new_item_transition": "default",
        "items": [
            {"media_id": img, "duration": 5, "scale_mode": "contain"},
            {"media_id": html, "duration": 8, "transition_mode": "none"},
            {"media_id": vid, "transition_mode": "custom", "transition_media_id": vid},
        ]}, headers=client.h)
    assert r.status_code == 200, r.json
    pl = r.json["playlist"]
    assert pl["transition_enabled"] and pl["transition_media"]["id"] == vid
    assert pl["items"][1]["transition_mode"] == "none"
    client.post(f"/api/playlists/{pid}/activate", json={}, headers=client.h)
    client.put("/api/settings", json={"scale_mode": "fill"}, headers=client.h)
    cfg = client.get("/api/player/v1/config", headers=AUTH).json
    items = cfg["items"]
    assert [i["kind"] for i in items] == ["image", "html", "video"]
    assert [i["scale"] for i in items] == ["contain", "fill", "fill"]
    assert [i["transition"] for i in items] == [vid, None, vid]
    assert items[1]["duration"] == 8 and items[1]["html"]["source"] == "upload"
    assert os.path.isdir(items[1]["html"]["path"])
    assert set(cfg["transitions"]) == {vid} and cfg["scale_default"] == "fill"
    # Overgangen uit => geen overgangen in de config
    client.put(f"/api/playlists/{pid}", json={"transition_enabled": False}, headers=client.h)
    cfg = client.get("/api/player/v1/config", headers=AUTH).json
    assert all(i["transition"] is None for i in cfg["items"]) and cfg["transitions"] == {}


def test_transition_must_be_video(client, media_files):
    img, vid, html, pid = _setup_playlist(client, media_files)
    r = client.put(f"/api/playlists/{pid}", json={"transition_enabled": True, "transition_media_id": img},
                   headers=client.h)
    assert r.status_code == 400
    r = client.put(f"/api/playlists/{pid}", json={"items": [{"media_id": img, "transition_mode": "custom"}]},
                   headers=client.h)
    assert r.status_code == 400


def test_missing_transition_does_not_block(client, media_files):
    img, vid, html, pid = _setup_playlist(client, media_files)
    client.put(f"/api/playlists/{pid}", json={"transition_enabled": True, "transition_media_id": vid,
                                               "items": [{"media_id": img}, {"media_id": html}]}, headers=client.h)
    os.unlink(os.path.join(client.cfg.files_dir, vid + ".mp4"))
    r = client.post(f"/api/playlists/{pid}/activate", json={}, headers=client.h)
    assert r.status_code == 200 and r.json["warnings"]
    cfg = client.get("/api/player/v1/config", headers=AUTH).json
    assert len(cfg["items"]) == 2 and all(i["transition"] is None for i in cfg["items"])


def test_delete_transition_media(client, media_files):
    img, vid, html, pid = _setup_playlist(client, media_files)
    client.put(f"/api/playlists/{pid}", json={"transition_enabled": True, "transition_media_id": vid,
                                               "items": [{"media_id": img, "transition_mode": "custom",
                                                          "transition_media_id": vid}]}, headers=client.h)
    r = client.delete(f"/api/media/{vid}", headers=client.h)
    assert r.status_code == 409 and r.json["playlists"][0]["roles"] == ["transition"]
    assert client.delete(f"/api/media/{vid}?force=1", headers=client.h).status_code == 200
    pl = client.get(f"/api/playlists/{pid}").json["playlist"]
    assert pl["transition_media_id"] is None
    assert pl["items"][0]["transition_mode"] == "default" and pl["items"][0]["transition_media_id"] is None
    assert pl["item_count"] == 1


def test_scale_setting_validation(client):
    assert client.put("/api/settings", json={"scale_mode": "zoom"}, headers=client.h).status_code == 400
    assert client.get("/api/settings").json["settings"]["scale_mode"] == "cover"


def test_html_media_backup_restore_reextracts(app_env, media_files):
    app, cfg, _ = app_env
    c = app.test_client()
    c.h = login(c, cfg)
    m = up_bytes(c, make_zip({"index.html": "<p>z</p>", "a.css": "x"}), "z.zip").json["media"]
    name = c.post("/api/backups", json={"include_media": True}, headers=c.h).json["name"]
    import shutil
    shutil.rmtree(os.path.join(cfg.html_dir, m["id"]))
    c.post(f"/api/backups/restore?name={name}", headers=c.h)
    from signage.app import create_app
    create_app(cfg, start_background=False)
    assert os.path.isfile(os.path.join(cfg.html_dir, m["id"], "index.html"))


# -- player: externe HTML-download en lokale playerserver ------------------------------------

def test_remote_player_downloads_html_project(client, tmp_path):
    import queue as q

    from signage.config import read_player_config
    from signage.player.sync import Syncer
    m = up_bytes(client, make_zip({"index.html": "<p>r</p>", "css/a.css": "x"}), "r.zip").json["media"]
    pid = client.post("/api/playlists", json={"name": "R"}, headers=client.h).json["id"]
    client.put(f"/api/playlists/{pid}", json={"items": [{"media_id": m["id"]}]}, headers=client.h)
    client.post(f"/api/playlists/{pid}/activate", json={}, headers=client.h)
    cfg = client.get("/api/player/v1/config", headers=AUTH).json
    archive = client.get(f"/api/player/v1/media/{m['id']}", headers=AUTH)
    assert archive.status_code == 200 and archive.data[:2] == b"PK"

    pcfg = read_player_config(str(tmp_path / "geen.ini"))
    s = Syncer(pcfg, str(tmp_path / "cache"), q.Queue(), lambda: {})
    s._download = lambda url, dst, sha, size: open(dst, "wb").write(archive.data)
    h = cfg["items"][0]["html"]
    h["path"] = None   # andere Pi: lokale map bestaat niet
    local = s.resolve_html(h)
    assert local.startswith(str(tmp_path / "cache")) and os.path.isfile(os.path.join(local, "css", "a.css"))


def test_local_player_server_security_and_ranges(tmp_path):
    import urllib.error
    import urllib.request

    from signage.player.browser import LocalServer, PageState
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "index.html").write_text("<p>x</p>")
    (tmp_path / "geheim.txt").write_text("geheim")
    vid = tmp_path / "v.mp4"
    vid.write_bytes(bytes(range(256)) * 40)
    import socket
    sck = socket.socket(); sck.bind(("127.0.0.1", 0)); port = sck.getsockname()[1]; sck.close()
    ps = PageState(port)
    ps.update({"revision": "1", "items": [
        {"media_id": "v", "name": "v", "kind": "video", "local": str(vid)},
        {"media_id": "h", "name": "h", "kind": "html", "html": {"source": "upload", "local": str(proj), "entry": "index.html"}},
        {"media_id": "c", "name": "c", "kind": "html", "html": {"source": "builtin", "page": "clock"}},
    ], "transitions": {}, "standby": {}})
    srv = LocalServer(port, ps, lambda d: None)
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        state = __import__("json").loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/state").read())
        assert [i["kind"] for i in state["items"]] == ["video", "html", "html"]
        assert state["items"][1]["src"].startswith(f"http://localhost:{port}/h/")
        req = urllib.request.Request(f"http://127.0.0.1:{port}{state['items'][0]['src']}", headers={"Range": "bytes=10-19"})
        r = urllib.request.urlopen(req)
        assert r.status == 206 and r.read() == bytes(range(10, 20))
        assert urllib.request.urlopen(state["items"][1]["src"]).read() == b"<p>x</p>"
        assert urllib.request.urlopen(state["items"][2]["src"]).status == 200
        # HTML-content (localhost) mag de status en configuratie van de player niet zien of wijzigen.
        for bad in (f"http://localhost:{port}/state", f"http://localhost:{port}/", f"http://localhost:{port}{state['items'][0]['src']}",
                    f"http://127.0.0.1:{port}" + state["items"][1]["src"].split(str(port), 1)[1]):
            try:
                urllib.request.urlopen(bad)
                assert False, bad
            except urllib.error.HTTPError as e:
                assert e.code == 404, bad
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://localhost:{port}/status", data=b"{}", method="POST"))
            assert False, "status via localhost"
        except urllib.error.HTTPError as e:
            assert e.code == 404
        for bad in ("/h/proj/../geheim.txt", "/h/proj/%2e%2e/geheim.txt", "/m/../geheim.txt", "/d/../player/player.js",
                    "/m/geheim.txt", "/etc/passwd"):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{bad}")
                assert False, bad
            except urllib.error.HTTPError as e:
                assert e.code == 404, bad
    finally:
        srv.shutdown()


def test_renderer_setting_api(client):
    info = client.get("/api/system/info").json
    assert info["renderer"]["setting"] == "auto" and info["renderer"]["fallback_active"] is False
    assert client.post("/api/system/renderer", json={"renderer": "chrome"}, headers=client.h).status_code == 400
    # Zonder root-hulpprogramma (testomgeving) wordt de wijziging netjes geweigerd.
    assert client.post("/api/system/renderer", json={"renderer": "mpv"}, headers=client.h).status_code == 501

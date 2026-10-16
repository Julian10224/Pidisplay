import io
import os
import tarfile

from PIL import Image

from conftest import login, upload


# -- authenticatie ----------------------------------------------------------------

def test_setup_requires_code(app_env):
    app, cfg, _ = app_env
    c = app.test_client()
    assert c.get("/api/auth/state").json["setup_required"] is True
    r = c.post("/api/auth/setup", json={"setup_code": "0000-0000", "username": "x" * 5, "password": "geheim123"})
    assert r.status_code == 403
    assert c.get("/api/media").status_code == 401


def test_setup_and_login_flow(app_env):
    app, cfg, _ = app_env
    c = app.test_client()
    login(c, cfg)
    assert not os.path.exists(cfg.setup_code_path), "setup-code moet na gebruik verdwijnen"
    # tweede setup is niet mogelijk
    assert c.post("/api/auth/setup", json={"setup_code": "x", "username": "abc", "password": "geheim123"}).status_code == 409
    c.post("/api/auth/logout")
    assert c.get("/api/media").status_code == 401
    assert c.post("/api/auth/login", json={"username": "beheer", "password": "fout-wachtwoord"}).status_code == 401
    assert c.post("/api/auth/login", json={"username": "beheer", "password": "geheim123"}).status_code == 200
    assert c.get("/api/media").status_code == 200


def test_password_is_hashed(client):
    from signage import db
    conn = db.connect(client.cfg.db_path)
    h = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert "geheim123" not in h and h.startswith("pbkdf2:sha256")


def test_csrf_required(client):
    r = client.post("/api/playlists", json={"name": "zonder token"})
    assert r.status_code == 403
    r = client.post("/api/playlists", json={"name": "met token"}, headers=client.h)
    assert r.status_code == 201


def test_login_throttling(app_env):
    app, cfg, _ = app_env
    c = app.test_client()
    login(c, cfg)
    c.post("/api/auth/logout")
    codes = [c.post("/api/auth/login", json={"username": "beheer", "password": "nee12345"}).status_code
             for _ in range(7)]
    assert 429 in codes


def test_public_client_blocked(app_env):
    app, cfg, _ = app_env
    c = app.test_client()
    r = c.get("/api/auth/state", environ_base={"REMOTE_ADDR": "8.8.8.8"})
    assert r.status_code == 403
    r = c.get("/api/auth/state", environ_base={"REMOTE_ADDR": "192.168.1.20"})
    assert r.status_code == 200


# -- media ----------------------------------------------------------------------

def test_upload_images(client, media_files):
    r = upload(client, media_files["png"])
    assert r.status_code == 201, r.json
    m = r.json["media"]
    assert m["kind"] == "image" and m["status"] == "ok" and m["has_thumb"]

    r = upload(client, media_files["jpg"])
    assert r.status_code == 201, r.json
    m = r.json["media"]
    # afspeelversie (1.1): EXIF-rotatie toegepast, groot genoeg om 1920x1080 beeldvullend te
    # bedekken, maar nooit groter dan 2048 px per zijde (GPU-limiet Pi 3).
    rend = os.path.join(client.cfg.renditions_dir, m["id"] + ".jpg")
    with Image.open(rend) as img:
        assert max(img.size) <= 2048
        assert img.size[1] > img.size[0], "portret na EXIF-rotatie"
        assert img.size == (1536, 2048)   # 3000x4000 -> cover 1920 breed, begrensd op 2048

    r = upload(client, media_files["webp"])
    assert r.status_code == 201, r.json


def test_upload_video_and_warning(client, media_files):
    r = upload(client, media_files["mp4"])
    assert r.status_code == 201, r.json
    m = r.json["media"]
    assert m["kind"] == "video" and m["codec"] == "h264" and m["status"] == "ok"
    assert 2.5 < m["duration"] < 3.5 and m["has_thumb"]

    r = upload(client, media_files["hevc"])
    assert r.status_code == 201, r.json
    assert r.json["media"]["status"] == "warning" and "hevc" in r.json["media"]["warning"]


def test_upload_rejects_invalid(client, media_files):
    assert upload(client, media_files["corrupt"]).status_code == 422
    assert upload(client, media_files["fake_png"]).status_code == 422
    r = upload(client, media_files["png"], name="script.sh")
    assert r.status_code == 415
    # niets achtergebleven in files/ of .incoming
    assert os.listdir(client.cfg.files_dir) == []
    assert os.listdir(client.cfg.incoming_dir) == []
    assert client.get("/api/media").json["media"] == []


def test_upload_path_traversal_name(client, media_files):
    r = upload(client, media_files["png"], name="../../etc/passwd.png")
    assert r.status_code == 201
    m = r.json["media"]
    assert m["name"] == "passwd" and m["original_filename"] == "passwd.png"
    assert os.listdir(client.cfg.files_dir) == [m["id"] + ".png"]


def test_upload_incomplete_leaves_nothing(client, media_files):
    with open(media_files["png"], "rb") as fh:
        data = fh.read()
    r = client.post("/api/media/upload", data=data[:100],
                    headers={**client.h, "X-Filename": "a.png", "Content-Length": str(len(data))},
                    content_type="application/octet-stream")
    # Afgebroken/afgekapte upload: geweigerd (400 onvolledig of 422 ongeldig), nooit opgeslagen.
    assert r.status_code in (400, 422)
    assert os.listdir(client.cfg.files_dir) == []
    assert os.listdir(client.cfg.incoming_dir) == []


def test_duplicate_upload(client, media_files):
    a = upload(client, media_files["png"]).json["media"]
    r = upload(client, media_files["png"])
    assert r.status_code == 200 and r.json["duplicate"] and r.json["media"]["id"] == a["id"]


def test_media_files_served_with_type(client, media_files):
    m = upload(client, media_files["mp4"]).json["media"]
    r = client.get(f"/api/media/{m['id']}/playback", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.headers["Content-Type"] == "video/mp4"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get(f"/api/media/{m['id']}/thumb").status_code == 200
    assert client.get("/api/media/../../etc/thumb").status_code == 404


def test_delete_media_in_use(client, media_files):
    m = upload(client, media_files["png"]).json["media"]
    pid = client.post("/api/playlists", json={"name": "P"}, headers=client.h).json["id"]
    client.put(f"/api/playlists/{pid}", json={"items": [{"media_id": m["id"], "duration": 5}]}, headers=client.h)
    r = client.delete(f"/api/media/{m['id']}", headers=client.h)
    assert r.status_code == 409 and r.json["playlists"][0]["name"] == "P"
    r = client.delete(f"/api/media/{m['id']}?force=1", headers=client.h)
    assert r.status_code == 200
    assert client.get(f"/api/playlists/{pid}").json["playlist"]["item_count"] == 0
    assert os.listdir(client.cfg.files_dir) == []


# -- playlists -----------------------------------------------------------------

def _playlist_with(client, media_files, kinds=("png", "mp4")):
    ids = [upload(client, media_files[k]).json["media"]["id"] for k in kinds]
    pid = client.post("/api/playlists", json={"name": "Hoofd"}, headers=client.h).json["id"]
    r = client.put(f"/api/playlists/{pid}", json={
        "repeat_mode": "loop", "items": [{"media_id": i, "duration": 7} for i in ids]}, headers=client.h)
    assert r.status_code == 200, r.json
    return pid, ids


def test_playlist_crud_and_activate(client, media_files):
    pid, ids = _playlist_with(client, media_files)
    pl = client.get(f"/api/playlists/{pid}").json["playlist"]
    assert [it["media"]["id"] for it in pl["items"]] == ids
    assert pl["items"][1]["duration"] is None, "video negeert duur"
    # volgorde wijzigen
    client.put(f"/api/playlists/{pid}", json={"items": [{"media_id": ids[1]}, {"media_id": ids[0], "duration": 3}]},
               headers=client.h)
    pl = client.get(f"/api/playlists/{pid}").json["playlist"]
    assert [it["media"]["id"] for it in pl["items"]] == [ids[1], ids[0]]
    assert client.post(f"/api/playlists/{pid}/activate", json={}, headers=client.h).status_code == 200
    lst = client.get("/api/playlists").json["playlists"]
    assert lst[0]["is_default"] and lst[0]["players"][0]["id"] == "local"
    # actieve playlist mag niet leeg worden
    r = client.put(f"/api/playlists/{pid}", json={"items": []}, headers=client.h)
    assert r.status_code == 422


def test_cannot_activate_empty_or_broken(client, media_files):
    pid = client.post("/api/playlists", json={"name": "Leeg"}, headers=client.h).json["id"]
    r = client.post(f"/api/playlists/{pid}/activate", json={}, headers=client.h)
    assert r.status_code == 422 and "leeg" in r.json["problems"][0]
    pid2, ids = _playlist_with(client, media_files, kinds=("png",))
    os.unlink(os.path.join(client.cfg.renditions_dir, ids[0] + ".png"))
    r = client.post(f"/api/playlists/{pid2}/activate", json={}, headers=client.h)
    assert r.status_code == 422 and "ontbreekt" in r.json["problems"][0]


def test_duplicate_playlist_name(client):
    assert client.post("/api/playlists", json={"name": "A"}, headers=client.h).status_code == 201
    assert client.post("/api/playlists", json={"name": "A"}, headers=client.h).status_code == 409


def test_delete_active_playlist_needs_force(client, media_files):
    pid, _ = _playlist_with(client, media_files)
    client.post(f"/api/playlists/{pid}/activate", json={}, headers=client.h)
    assert client.delete(f"/api/playlists/{pid}", headers=client.h).status_code == 409
    assert client.delete(f"/api/playlists/{pid}?force=1", headers=client.h).status_code == 200
    assert client.get("/api/settings").json["settings"]["default_playlist_id"] is None


# -- player-API ----------------------------------------------------------------

def test_player_config_and_etag(client, media_files):
    pid, ids = _playlist_with(client, media_files)
    client.post(f"/api/playlists/{pid}/activate", json={}, headers=client.h)
    auth = {"Authorization": "Bearer local-test-token-0123456789"}
    r = client.get("/api/player/v1/config", headers=auth)
    assert r.status_code == 200
    cfg = r.json
    assert [i["media_id"] for i in cfg["items"]] == ids
    assert cfg["items"][0]["duration"] == 7 and cfg["items"][1]["kind"] == "video"
    assert os.path.exists(cfg["items"][0]["path"])
    r2 = client.get("/api/player/v1/config", headers={**auth, "If-None-Match": r.headers["ETag"]})
    assert r2.status_code == 304
    # wijziging => nieuwe revisie
    client.put("/api/settings", json={"volume": 20}, headers=client.h)
    r3 = client.get("/api/player/v1/config", headers={**auth, "If-None-Match": r.headers["ETag"]})
    assert r3.status_code == 200 and r3.json["volume"] == 20
    assert client.get("/api/player/v1/config", headers={"Authorization": "Bearer fout"}).status_code == 401
    assert client.get(f"/api/player/v1/media/{ids[0]}", headers=auth).status_code == 200


def test_heartbeat_and_status(client):
    auth = {"Authorization": "Bearer local-test-token-0123456789"}
    st = client.get("/api/status").json
    assert any("meldt zich niet" in w["text"] for w in st["warnings"])
    r = client.post("/api/player/v1/heartbeat", json={"state": "playing", "current": {"name": "x"}}, headers=auth)
    assert r.status_code == 200
    st = client.get("/api/status").json
    local = [p for p in st["players"] if p["id"] == "local"][0]
    assert local["online"] and local["status"]["current"]["name"] == "x"


def test_remote_player_registration(client, media_files):
    r = client.post("/api/players", json={"name": "Kantine"}, headers=client.h)
    assert r.status_code == 201
    token = r.json["token"]
    auth = {"Authorization": "Bearer " + token}
    assert client.get("/api/player/v1/config", headers=auth).status_code == 200
    r = client.patch(f"/api/players/{r.json['id']}", json={"rotation": 90}, headers=client.h)
    assert r.json["player"]["rotation"] == 90
    assert client.get("/api/player/v1/config", headers=auth).json["player"]["rotation"] == 90
    assert client.delete("/api/players/local", headers=client.h).status_code == 400


# -- instellingen en back-up -----------------------------------------------------------

def test_settings_validation(client):
    assert client.put("/api/settings", json={"default_image_duration": 0}, headers=client.h).status_code == 400
    r = client.put("/api/settings", json={"system_name": "Receptie", "default_image_duration": 12}, headers=client.h)
    assert r.json["settings"]["system_name"] == "Receptie"
    assert client.get("/api/auth/state").json["system_name"] == "Receptie"


def test_system_actions_without_helper(client):
    r = client.post("/api/system/reboot", json={}, headers=client.h)
    assert r.status_code == 501
    assert client.post("/api/system/hostname", json={"hostname": "Bad Name!"}, headers=client.h).status_code == 400


def test_backup_and_restore(app_env, media_files):
    app, cfg, _ = app_env
    c = app.test_client()
    c.h = login(c, cfg)
    m = upload(c, media_files["png"]).json["media"]
    c.post("/api/playlists", json={"name": "Bewaren"}, headers=c.h)
    r = c.post("/api/backups", json={"include_media": True}, headers=c.h)
    assert r.status_code == 201
    name = r.json["name"]
    data = c.get(f"/api/backups/{name}").data
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        names = tar.getnames()
    assert "signage.db" in names and f"media/files/{m['id']}.png" in names
    # wijzigen na de back-up
    c.post("/api/playlists", json={"name": "Later"}, headers=c.h)
    # ongeldig archief geweigerd
    assert c.post("/api/backups/restore", data=b"rommel", headers=c.h).status_code == 422
    r = c.post("/api/backups/restore", data=data, headers=c.h, content_type="application/gzip")
    assert r.status_code == 200
    assert os.path.exists(cfg.restore_pending_path)
    # 'herstart': nieuwe app-instantie past herstel toe
    from signage.app import create_app
    app2 = create_app(cfg, start_background=False)
    c2 = app2.test_client()
    c2.post("/api/auth/login", json={"username": "beheer", "password": "geheim123"})
    names = [p["name"] for p in c2.get("/api/playlists").json["playlists"]]
    assert names == ["Bewaren"]
    assert any(b["pre_restore"] for b in c2.get("/api/backups").json["backups"])


def test_malicious_backup_rejected(tmp_path, app_env):
    _app, cfg, _ = app_env
    from signage import backup
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        info = tarfile.TarInfo("../../etc/evil")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    try:
        backup.validate_archive(str(bad))
        assert False, "moet geweigerd worden"
    except backup.BackupError:
        pass


def test_interrupted_upload_cleaned_on_start(tmp_path, monkeypatch):
    from conftest import make_config
    ini, _ = make_config(tmp_path)
    from signage.app import create_app
    from signage.config import Config
    cfg = Config(ini)
    cfg.ensure_dirs()
    leftover = os.path.join(cfg.incoming_dir, "abc.part")
    open(leftover, "wb").write(b"half")
    create_app(cfg, start_background=False)
    assert not os.path.exists(leftover)


def test_spa_routes(client):
    assert client.get("/").status_code == 200
    assert b"<div id=\"app\">" in client.get("/").data
    assert client.get("/static/app.js").status_code == 200

"""Tests voor 1.2: willekeurige volgorde, logo per item en het media-overzicht."""

import os
import sqlite3

from PIL import Image

from conftest import make_config, upload

AUTH = {"Authorization": "Bearer local-test-token-0123456789"}
HERE = os.path.dirname(os.path.abspath(__file__))


def _png(tmp_path, name, alpha=True, size=(300, 120)):
    p = tmp_path / name
    Image.new("RGBA" if alpha else "RGB", size, (255, 255, 255, 0) if alpha else (10, 10, 10)).save(p, "PNG")
    return str(p)


def _jpg(tmp_path, name):
    p = tmp_path / name
    Image.new("RGB", (300, 120), (200, 0, 0)).save(p, "JPEG")
    return str(p)


# -- migratie 2 -> 3 --------------------------------------------------------------------

def test_migration_from_v2(tmp_path):
    ini, _ = make_config(tmp_path)
    from signage.config import Config
    cfg = Config(ini)
    cfg.ensure_dirs()
    conn = sqlite3.connect(cfg.db_path)
    conn.executescript(open(os.path.join(HERE, "v2_schema.sql")).read())
    conn.execute("INSERT INTO meta VALUES ('schema_version', '2')")
    conn.execute("INSERT INTO meta VALUES ('renditions_version', '2')")
    conn.execute("INSERT INTO playlists (id, name, created_at, updated_at, transition_enabled) VALUES (1, 'Oud', 1, 1, 1)")
    conn.commit()
    conn.close()
    from signage.app import create_app
    create_app(cfg, start_background=False)
    conn = sqlite3.connect(cfg.db_path)
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "3"
    assert conn.execute("SELECT shuffle, transition_enabled FROM playlists").fetchone() == (0, 1)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(playlist_items)")]
    assert "logo_media_id" in cols
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    assert any(b.startswith("signage-backup-pre-migration-") for b in os.listdir(cfg.backups_dir))


def test_migration_from_v1_runs_all_steps(tmp_path):
    ini, _ = make_config(tmp_path)
    from signage.config import Config
    cfg = Config(ini)
    cfg.ensure_dirs()
    conn = sqlite3.connect(cfg.db_path)
    conn.executescript(open(os.path.join(HERE, "v1_schema.sql")).read())
    conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    conn.commit()
    conn.close()
    from signage.app import create_app
    create_app(cfg, start_background=False)
    conn = sqlite3.connect(cfg.db_path)
    assert conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == "3"
    cols = [r[1] for r in conn.execute("PRAGMA table_info(playlists)")]
    assert "shuffle" in cols and "transition_enabled" in cols


# -- willekeurige volgorde en logo ---------------------------------------------------------

def _playlist(client, media_files, tmp_path):
    a = upload(client, media_files["png"]).json["media"]["id"]
    b = upload(client, media_files["mp4"]).json["media"]["id"]
    logo = upload(client, _png(tmp_path, "logo.png")).json["media"]
    pid = client.post("/api/playlists", json={"name": "Mix"}, headers=client.h).json["id"]
    return a, b, logo, pid


def test_shuffle_and_logo_saved_and_sent_to_player(client, media_files, tmp_path):
    a, b, logo, pid = _playlist(client, media_files, tmp_path)
    assert logo["transparent"] is True
    r = client.put(f"/api/playlists/{pid}", json={"shuffle": True, "items": [
        {"media_id": a, "logo_media_id": logo["id"]}, {"media_id": b}]}, headers=client.h)
    assert r.status_code == 200, r.json
    pl = r.json["playlist"]
    assert pl["shuffle"] is True
    assert pl["items"][0]["logo_media"]["id"] == logo["id"] and pl["items"][1]["logo_media_id"] is None
    client.post(f"/api/playlists/{pid}/activate", json={}, headers=client.h)
    cfg = client.get("/api/player/v1/config", headers=AUTH).json
    assert cfg["playlist"]["shuffle"] is True
    lg = cfg["items"][0]["logo"]
    assert lg["media_id"] == logo["id"] and lg["ext"] == ".png" and os.path.exists(lg["path"])
    assert cfg["items"][1]["logo"] is None
    assert client.get(f"/api/player/v1/media/{logo['id']}", headers=AUTH).status_code == 200
    # uitzetten verandert de revisie
    rev = cfg["revision"]
    client.put(f"/api/playlists/{pid}", json={"shuffle": False}, headers=client.h)
    assert client.get("/api/player/v1/config", headers=AUTH).json["revision"] != rev


def test_logo_must_be_png(client, media_files, tmp_path):
    a, b, logo, pid = _playlist(client, media_files, tmp_path)
    jpg = upload(client, _jpg(tmp_path, "logo.jpg")).json["media"]["id"]
    for bad in (jpg, b, "f" * 32):
        r = client.put(f"/api/playlists/{pid}", json={"items": [{"media_id": a, "logo_media_id": bad}]}, headers=client.h)
        assert r.status_code == 400, bad
    # PNG zonder transparantie mag wel
    opaque = upload(client, _png(tmp_path, "vlak.png", alpha=False)).json["media"]
    assert opaque["transparent"] is False
    assert client.put(f"/api/playlists/{pid}", json={"items": [{"media_id": a, "logo_media_id": opaque["id"]}]},
                      headers=client.h).status_code == 200


def test_deleting_logo_keeps_items(client, media_files, tmp_path):
    a, b, logo, pid = _playlist(client, media_files, tmp_path)
    client.put(f"/api/playlists/{pid}", json={"items": [{"media_id": a, "logo_media_id": logo["id"]}]}, headers=client.h)
    r = client.delete(f"/api/media/{logo['id']}", headers=client.h)
    assert r.status_code == 409 and r.json["playlists"][0]["roles"] == ["logo"]
    assert client.delete(f"/api/media/{logo['id']}?force=1", headers=client.h).status_code == 200
    pl = client.get(f"/api/playlists/{pid}").json["playlist"]
    assert pl["item_count"] == 1 and pl["items"][0]["logo_media_id"] is None


def test_missing_logo_is_warning_not_blocking(client, media_files, tmp_path):
    a, b, logo, pid = _playlist(client, media_files, tmp_path)
    client.put(f"/api/playlists/{pid}", json={"items": [{"media_id": a, "logo_media_id": logo["id"]}]}, headers=client.h)
    for f in os.listdir(client.cfg.renditions_dir):
        if f.startswith(logo["id"]):
            os.unlink(os.path.join(client.cfg.renditions_dir, f))
    r = client.post(f"/api/playlists/{pid}/activate", json={}, headers=client.h)
    assert r.status_code == 200 and any("logo" in w for w in r.json["warnings"])
    assert client.get("/api/player/v1/config", headers=AUTH).json["items"][0]["logo"] is None


# -- media-overzicht ---------------------------------------------------------------------

def test_media_overview_summary_and_usage(client, media_files, tmp_path):
    a, b, logo, pid = _playlist(client, media_files, tmp_path)
    client.put(f"/api/playlists/{pid}", json={"transition_enabled": True, "transition_media_id": b,
                                               "items": [{"media_id": a, "logo_media_id": logo["id"]}]}, headers=client.h)
    r = client.get("/api/media").json
    by = {m["id"]: m for m in r["media"]}
    assert by[a]["playlists"][0]["roles"] == ["item"]
    assert by[b]["playlists"][0]["roles"] == ["transition"]
    assert by[logo["id"]]["playlists"][0]["roles"] == ["logo"]
    assert all(m["storage_bytes"] > 0 for m in r["media"])
    s = r["summary"]
    assert s["counts"] == {"image": 2, "video": 1, "html": 0} and s["unused"] == 0
    assert s["media_bytes"] == sum(m["storage_bytes"] for m in r["media"]) and s["disk_free"] > 0


def test_bulk_delete(client, media_files, tmp_path):
    a, b, logo, pid = _playlist(client, media_files, tmp_path)
    jpg = upload(client, _jpg(tmp_path, "los.jpg")).json["media"]["id"]
    client.put(f"/api/playlists/{pid}", json={"items": [{"media_id": a}]}, headers=client.h)
    r = client.post("/api/media/delete", json={"ids": [a, jpg, logo["id"]]}, headers=client.h)
    assert r.status_code == 409 and [e["id"] for e in r.json["in_use"]] == [a]
    assert len(client.get("/api/media").json["media"]) == 4, "niets verwijderd zonder bevestiging"
    r = client.post("/api/media/delete", json={"ids": [a, jpg, logo["id"]], "force": True}, headers=client.h)
    assert r.status_code == 200 and r.json["deleted"] == 3
    assert [m["id"] for m in client.get("/api/media").json["media"]] == [b]
    assert client.get(f"/api/playlists/{pid}").json["playlist"]["item_count"] == 0
    remaining = os.listdir(client.cfg.files_dir)
    assert remaining == [b + ".mp4"]
    assert client.post("/api/media/delete", json={"ids": []}, headers=client.h).status_code == 400
    assert client.post("/api/media/delete", json={"ids": ["../etc"]}, headers=client.h).status_code == 404

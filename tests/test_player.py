"""Player-tests met een nagebootste mpv (tests/fake_mpv.py).

Deze tests controleren de afspeellogica, herstel en synchronisatie. Ze testen NIET
de echte weergave op een Raspberry Pi (GPU, HDMI, hardwaredecodering).
"""

import json
import os
import socket
import threading
import time

import pytest
from PIL import Image

from conftest import login, make_config, upload

HERE = os.path.dirname(os.path.abspath(__file__))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def read_log(path):
    try:
        with open(path) as fh:
            return [l.strip() for l in fh if l.strip()]
    except FileNotFoundError:
        return []


def loads(path):
    return [l for l in read_log(path) if not l.startswith("set ")]


def wait_for(cond, timeout=15, interval=0.1):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(interval)
    return False


class PlayerThread:
    def __init__(self, player_ini, monkeypatch=None):
        from signage.config import read_player_config
        from signage.player.daemon import Player
        self.player = Player(read_player_config(player_ini))
        self.player.PING_INTERVAL = 0.3
        self.t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        p = self.player
        cached = p.load_cache()
        if cached:
            p.queue.put((cached, []))
        p.syncer.start()
        while p.running:
            p.tick()
            time.sleep(0.05)
        p.syncer.stop()
        p.mpv.stop()

    def start(self):
        self.t.start()
        return self

    def stop(self):
        self.player.running = False
        self.t.join(timeout=10)


@pytest.fixture
def fake_log(tmp_path, monkeypatch):
    log = tmp_path / "mpv.log"
    monkeypatch.setenv("FAKE_MPV_LOG", str(log))
    monkeypatch.setenv("FAKE_MPV_VIDEO_SECONDS", "1.0")
    return str(log)


def write_cache(cache_dir, items, standby=None, **extra):
    os.makedirs(cache_dir, exist_ok=True)
    cfg = {"revision": "test", "playlist": {"id": 1, "name": "T", "repeat_mode": "loop", "mute": False},
           "items": items, "fallback_items": [], "volume": 50, "player": {"id": "local", "rotation": 0},
           "standby": {"local": standby}}
    cfg.update(extra)
    with open(os.path.join(cache_dir, "last-config.json"), "w") as fh:
        json.dump(cfg, fh)


def img(tmp_path, name):
    p = tmp_path / name
    Image.new("RGB", (64, 64)).save(p, "PNG")
    return str(p)


# -- afspeellogica zonder server --------------------------------------------------

def test_plays_cached_playlist_without_server(tmp_path, fake_log):
    _ini, player_ini = make_config(tmp_path, port=free_port())
    a, b = img(tmp_path, "a.png"), img(tmp_path, "b.png")
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    write_cache(str(tmp_path / "player-cache"), [
        {"media_id": "1", "name": "a", "kind": "image", "local": a, "duration": 0.6},
        {"media_id": "2", "name": "v", "kind": "video", "local": str(v), "video_duration": 1.0},
        {"media_id": "3", "name": "b", "kind": "image", "local": b, "duration": 0.6},
    ])
    pt = PlayerThread(player_ini).start()
    try:
        assert wait_for(lambda: len(loads(fake_log)) >= 7, timeout=20)
        seq = loads(fake_log)[:7]
        assert seq == ["a.png", "v.mp4", "b.png", "a.png", "v.mp4", "b.png", "a.png"], seq
        assert "set volume=50" in read_log(fake_log)
    finally:
        pt.stop()


def test_skips_missing_and_corrupt_files(tmp_path, fake_log):
    _ini, player_ini = make_config(tmp_path, port=free_port())
    good = img(tmp_path, "goed.png")
    corrupt = img(tmp_path, "corrupt.png")
    write_cache(str(tmp_path / "player-cache"), [
        {"media_id": "1", "name": "weg", "kind": "image", "local": str(tmp_path / "bestaatniet.png"), "duration": 1},
        {"media_id": "2", "name": "kapot", "kind": "image", "local": corrupt, "duration": 1},
        {"media_id": "3", "name": "goed", "kind": "image", "local": good, "duration": 0.5},
    ])
    pt = PlayerThread(player_ini).start()
    try:
        assert wait_for(lambda: loads(fake_log).count("goed.png") >= 2, timeout=25)
        assert any("niet afspelen" in e for e in pt.player.errors)
        assert any("ontbreekt" in e for e in pt.player.errors)
        assert pt.player.mpv.alive()
    finally:
        pt.stop()


def test_all_broken_shows_standby(tmp_path, fake_log):
    _ini, player_ini = make_config(tmp_path, port=free_port())
    corrupt = img(tmp_path, "corrupt.png")
    sb = img(tmp_path, "standby.png")
    write_cache(str(tmp_path / "player-cache"),
                [{"media_id": "1", "name": "kapot", "kind": "image", "local": corrupt, "duration": 1}], standby=sb)
    pt = PlayerThread(player_ini).start()
    try:
        assert wait_for(lambda: "standby.png" in loads(fake_log), timeout=15)
        assert pt.player.status()["state"] == "standby"
    finally:
        pt.stop()


def test_empty_playlist_shows_standby(tmp_path, fake_log):
    _ini, player_ini = make_config(tmp_path, port=free_port())
    sb = img(tmp_path, "standby.png")
    write_cache(str(tmp_path / "player-cache"), [], standby=sb)
    pt = PlayerThread(player_ini).start()
    try:
        assert wait_for(lambda: loads(fake_log) == ["standby.png"], timeout=10)
    finally:
        pt.stop()


def test_hung_mpv_is_restarted(tmp_path, fake_log):
    _ini, player_ini = make_config(tmp_path, port=free_port())
    hang = img(tmp_path, "hang.png")
    good = img(tmp_path, "goed.png")
    write_cache(str(tmp_path / "player-cache"), [
        {"media_id": "1", "name": "hang", "kind": "image", "local": hang, "duration": 5},
        {"media_id": "2", "name": "goed", "kind": "image", "local": good, "duration": 0.5},
    ])
    pt = PlayerThread(player_ini).start()
    try:
        assert wait_for(lambda: "goed.png" in loads(fake_log), timeout=30)
        assert pt.player.total_restarts >= 1
        assert any("herstart" in e for e in pt.player.errors)
    finally:
        pt.stop()


def test_once_hold_last(tmp_path, fake_log):
    _ini, player_ini = make_config(tmp_path, port=free_port())
    a, b = img(tmp_path, "a.png"), img(tmp_path, "b.png")
    write_cache(str(tmp_path / "player-cache"), [
        {"media_id": "1", "name": "a", "kind": "image", "local": a, "duration": 0.4},
        {"media_id": "2", "name": "b", "kind": "image", "local": b, "duration": 0.4},
    ], playlist={"id": 1, "name": "T", "repeat_mode": "once_hold_last", "mute": False})
    pt = PlayerThread(player_ini).start()
    try:
        time.sleep(3)
        assert loads(fake_log) == ["a.png", "b.png"]
        assert pt.player.mode == "hold"
    finally:
        pt.stop()


# -- samen met de echte server -------------------------------------------------------

@pytest.fixture
def live_server(tmp_path, monkeypatch, media_files):
    port = free_port()
    ini, player_ini = make_config(tmp_path, port=port)
    monkeypatch.setenv("SIGNAGE_CONFIG", ini)
    from waitress import create_server

    from signage.app import create_app
    from signage.config import Config
    cfg = Config(ini)
    app = create_app(cfg, start_background=False)
    srv = create_server(app, host="127.0.0.1", port=port, threads=4)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    c = app.test_client()
    c.h = login(c, cfg)
    c.cfg = cfg
    yield c, player_ini, srv, cfg
    srv.close()


def test_sync_switch_and_offline(live_server, media_files, fake_log, tmp_path):
    c, player_ini, srv, cfg = live_server
    img_id = upload(c, media_files["png"]).json["media"]["id"]
    vid_id = upload(c, media_files["mp4"]).json["media"]["id"]
    p1 = c.post("/api/playlists", json={"name": "Een"}, headers=c.h).json["id"]
    c.put(f"/api/playlists/{p1}", json={"items": [{"media_id": img_id, "duration": 1}]}, headers=c.h)
    c.post(f"/api/playlists/{p1}/activate", json={}, headers=c.h)

    pt = PlayerThread(player_ini).start()
    try:
        assert wait_for(lambda: img_id + ".png" in loads(fake_log), timeout=15)
        # heartbeat komt binnen
        assert wait_for(lambda: c.get("/api/status").json["players"][0]["online"], timeout=15)

        # nieuwe playlist activeren => player schakelt zonder herstart over
        p2 = c.post("/api/playlists", json={"name": "Twee"}, headers=c.h).json["id"]
        c.put(f"/api/playlists/{p2}", json={"items": [{"media_id": vid_id}]}, headers=c.h)
        c.post(f"/api/playlists/{p2}/activate", json={}, headers=c.h)
        assert wait_for(lambda: vid_id + ".mp4" in loads(fake_log), timeout=15)
        restarts_before = pt.player.total_restarts

        # server weg => player blijft afspelen
        srv.close()
        n = loads(fake_log).count(vid_id + ".mp4")
        assert wait_for(lambda: loads(fake_log).count(vid_id + ".mp4") >= n + 2, timeout=15)
        assert pt.player.total_restarts == restarts_before
    finally:
        pt.stop()

    # nieuwe start zonder server => laatst bekende playlist uit cache
    open(fake_log, "w").close()
    pt2 = PlayerThread(player_ini).start()
    try:
        assert wait_for(lambda: vid_id + ".mp4" in loads(fake_log), timeout=15)
    finally:
        pt2.stop()


def test_remote_player_downloads_media(live_server, media_files, tmp_path):
    c, _player_ini, srv, cfg = live_server
    img_id = upload(c, media_files["png"]).json["media"]["id"]
    p1 = c.post("/api/playlists", json={"name": "Een"}, headers=c.h).json["id"]
    c.put(f"/api/playlists/{p1}", json={"items": [{"media_id": img_id}]}, headers=c.h)
    c.post(f"/api/playlists/{p1}/activate", json={}, headers=c.h)
    token = c.post("/api/players", json={"name": "Extern"}, headers=c.h).json["token"]

    import queue as q

    from signage.config import read_player_config
    from signage.player.daemon import Syncer
    pcfg = read_player_config(_player_ini)
    pcfg["token"] = token
    remote_cache = str(tmp_path / "remote-cache")

    class RemoteSyncer(Syncer):
        def process_config(self, cfg_):
            for it in cfg_["items"]:
                it["path"] = None      # simuleer een andere Pi: lokale paden bestaan niet
            cfg_["standby"]["path"] = None
            return super().process_config(cfg_)

    out = q.Queue()
    s = RemoteSyncer(pcfg, remote_cache, out, lambda: {})
    os.makedirs(remote_cache, exist_ok=True)
    s.fetch_once()
    got, errors = out.get(timeout=5)
    if errors:
        # Het standby-scherm kan net opnieuw getekend zijn (na de setup); de volgende
        # synchronisatieronde haalt dan de nieuwe versie op. Dat is het bedoelde herstel.
        s.revision = None
        s.fetch_once()
        got, errors = out.get(timeout=5)
    assert errors == []
    local = got["items"][0]["local"]
    assert local.startswith(remote_cache) and os.path.getsize(local) == got["items"][0]["size"]
    assert got["standby"]["local"].startswith(remote_cache)
    assert os.path.exists(os.path.join(remote_cache, "last-config.json"))

import os
import subprocess
import sys

import pytest
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))


def make_config(tmp_path, port=8080, extra=""):
    data = tmp_path / "data"
    ini = tmp_path / "signage.ini"
    player_ini = tmp_path / "player.ini"
    player_ini.write_text(
        "[player]\n"
        f"server_url = http://127.0.0.1:{port}\n"
        "token = local-test-token-0123456789\n"
        f"cache_dir = {tmp_path / 'player-cache'}\n"
        f"mpv_binary = {os.path.join(HERE, 'fake_mpv.py')}\n"
        "poll_interval = 2\nheartbeat_interval = 5\n")
    ini.write_text(
        "[server]\n"
        f"port = {port}\nbind = 127.0.0.1\n"
        "[storage]\n"
        f"data_dir = {data}\nmin_free_mb = 1\nmax_upload_mb = 200\n"
        "[system]\n"
        "priv_helper =\n"
        f"local_player_config = {player_ini}\n" + extra)
    return str(ini), str(player_ini)


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    ini, player_ini = make_config(tmp_path)
    monkeypatch.setenv("SIGNAGE_CONFIG", ini)
    from signage.app import create_app
    from signage.config import Config
    cfg = Config(ini)
    app = create_app(cfg, start_background=False)
    app.config["TESTING"] = True
    return app, cfg, tmp_path


def login(client, cfg):
    code = open(cfg.setup_code_path).read().strip()
    r = client.post("/api/auth/setup", json={"setup_code": code, "username": "beheer", "password": "geheim123"})
    assert r.status_code == 200, r.json
    st = client.get("/api/auth/state").json
    assert st["logged_in"]
    return {"X-CSRF-Token": st["csrf"]}


@pytest.fixture
def client(app_env):
    app, cfg, tmp = app_env
    c = app.test_client()
    headers = login(c, cfg)
    c.h = headers
    c.cfg = cfg
    return c


# -- testmedia -----------------------------------------------------------------
MEDIA_DIR = os.path.join(HERE, ".media")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def media_files():
    os.makedirs(MEDIA_DIR, exist_ok=True)
    out = {}
    p = os.path.join(MEDIA_DIR, "kleur.png")
    if not os.path.exists(p):
        Image.new("RGBA", (800, 600), (200, 30, 30, 128)).save(p)
    out["png"] = p
    p = os.path.join(MEDIA_DIR, "groot.jpg")
    if not os.path.exists(p):
        img = Image.new("RGB", (4000, 3000), (20, 120, 200))
        exif = img.getexif()
        exif[0x0112] = 6  # 90 graden gedraaid
        img.save(p, "JPEG", exif=exif.tobytes())
    out["jpg"] = p
    p = os.path.join(MEDIA_DIR, "foto.webp")
    if not os.path.exists(p):
        Image.new("RGB", (640, 480), (10, 200, 10)).save(p, "WEBP")
    out["webp"] = p
    p = os.path.join(MEDIA_DIR, "clip.mp4")
    if not os.path.exists(p):
        _ffmpeg("-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25", "-f", "lavfi", "-i", "sine=f=440",
                "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", p)
    out["mp4"] = p
    p = os.path.join(MEDIA_DIR, "hevc.mp4")
    if not os.path.exists(p):
        _ffmpeg("-f", "lavfi", "-i", "testsrc=size=640x360:rate=25", "-t", "1", "-c:v", "libx265",
                "-x265-params", "log-level=error", p)
    out["hevc"] = p
    p = os.path.join(MEDIA_DIR, "kapot.mp4")
    if not os.path.exists(p):
        with open(out["mp4"], "rb") as fh:
            data = fh.read()
        with open(p, "wb") as fh:
            fh.write(os.urandom(2048) + data[5000:9000])
    out["corrupt"] = p
    p = os.path.join(MEDIA_DIR, "nep.png")
    if not os.path.exists(p):
        with open(p, "w") as fh:
            fh.write("<html><script>alert(1)</script></html>")
    out["fake_png"] = p
    return out


def upload(client, path, name=None):
    from urllib.parse import quote
    with open(path, "rb") as fh:
        data = fh.read()
    return client.post("/api/media/upload", data=data,
                       headers={**client.h, "X-Filename": quote(name or os.path.basename(path)),
                                "Content-Type": "application/octet-stream"})

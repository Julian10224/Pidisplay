"""Tests voor 1.3.2: software bijwerken via de website en de foutmelding van de browserweergave."""

import json
import os
import time

from signage import routes_update as ru


def test_version_compare_and_notes():
    assert ru.is_newer("1.3.2", "1.3.1") and ru.is_newer("1.10.0", "1.9.9")
    assert not ru.is_newer("1.3.1", "1.3.1") and not ru.is_newer("1.2.0", "1.3.1")
    assert ru.is_newer("1.3.2", "1.3.1-local")
    log = "# Wijzigingen\n\n## 1.4.0 — nieuw\n\n- a\n\n## 1.3.2 — fix\n\n- b\n\n## 1.3.1 — oud\n\n- c\n"
    notes = ru.release_notes(log, "1.3.1")
    assert "1.4.0" in notes and "- b" in notes and "- c" not in notes and "Wijzigingen" not in notes


def test_update_api(client, monkeypatch):
    r = client.get("/api/system/update").json
    assert r["enabled"] and r["repo"] == "Julian10224/Pidisplay" and r["branch"] == "main"
    assert r["status"] is None and r["check"] is None
    pages = {"VERSION": "99.0.0\n", "CHANGELOG.md": "# W\n\n## 99.0.0 — groot\n\n- alles nieuw\n"}
    seen = []

    def fake_fetch(url, timeout=10):
        seen.append(url)
        return pages[url.rsplit("/", 1)[1]]
    monkeypatch.setattr(ru, "_fetch", fake_fetch)
    r = client.post("/api/system/update/check", headers=client.h).json["check"]
    assert r["latest"] == "99.0.0" and r["newer"] and "alles nieuw" in r["notes"]
    assert seen[0] == "https://raw.githubusercontent.com/Julian10224/Pidisplay/main/VERSION"
    assert client.get("/api/system/update").json["check"]["latest"] == "99.0.0"
    # zonder root-hulpprogramma (testomgeving) wordt bijwerken netjes geweigerd
    assert client.post("/api/system/update", headers=client.h).status_code == 501
    # zonder CSRF-token geen update
    assert client.post("/api/system/update").status_code == 403

    def offline(url, timeout=10):
        raise OSError("Network is unreachable")
    monkeypatch.setattr(ru, "_fetch", offline)
    r = client.post("/api/system/update/check", headers=client.h)
    assert r.status_code == 502 and "internet" in r.json["error"]


def test_update_disabled_and_invalid_source(tmp_path, monkeypatch):
    from conftest import login, make_config
    from signage.app import create_app
    from signage.config import Config
    for extra in ("[update]\nenabled = false\n", "[update]\nrepo = ../evil;rm\n"):
        sub = tmp_path / str(abs(hash(extra)))
        sub.mkdir()
        ini, _ = make_config(sub, extra=extra)
        app = create_app(Config(ini), start_background=False)
        c = app.test_client()
        h = login(c, Config(ini))
        assert c.get("/api/system/update").json["enabled"] is False
        assert c.post("/api/system/update", headers=h).status_code == 400
        assert c.post("/api/system/update/check", headers=h).status_code == 400


def test_update_status_and_interrupted(client, monkeypatch):
    d = os.path.join(client.cfg.data_dir, "update")
    os.makedirs(d)
    st = {"state": "running", "message": "Downloaden…", "from": "1.3.1", "to": None,
          "started": time.time() - 600, "updated": time.time() - 500}
    with open(os.path.join(d, "status.json"), "w") as fh:
        json.dump(st, fh)
    with open(os.path.join(d, "update.log"), "w") as fh:
        fh.write("regel 1\nregel 2\n")
    monkeypatch.setattr(ru, "_service_active", lambda: False)
    r = client.get("/api/system/update").json
    assert r["status"]["state"] == "interrupted" and r["log"].startswith("regel 1")
    monkeypatch.setattr(ru, "_service_active", lambda: True)
    assert client.get("/api/system/update").json["status"]["state"] == "running"


def test_renderer_info_shows_last_failure(client):
    from signage.config import read_player_config
    pcfg = read_player_config(client.cfg.local_player_config)
    os.makedirs(pcfg.get("cache_dir"), exist_ok=True)
    with open(os.path.join(pcfg.get("cache_dir"), "browser-failures"), "w") as fh:
        fh.write("3\n")
    with open(os.path.join(pcfg.get("cache_dir"), "browser-last-failure.log"), "w") as fh:
        fh.write("Browserweergave stopte na 1 s\n--- Laatste uitvoer ---\nUnable to create the wlroots backend\n")
    r = client.get("/api/system/info").json["renderer"]
    assert r["fallback_active"] and "wlroots" in r["last_failure"] and r["last_failure_at"] > 0


def test_launcher_saves_failure_output(tmp_path):
    """signage-player-launch bewaart de uitvoer van een mislukte browserstart (nagebootste cage)."""
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "cage").write_text("#!/bin/bash\necho '[ERROR] [libseat] Could not activate session: Permission denied' >&2\n"
                               "echo 'Unable to create the wlroots backend' >&2\nexit 1\n")
    (fake / "chromium").write_text("#!/bin/bash\necho 'Chromium 130.0'\n")
    for f in fake.iterdir():
        f.chmod(0o755)
    (tmp_path / "p.ini").write_text("[player]\nrenderer = browser\n")
    state = tmp_path / "state"
    env = dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}", SIGNAGE_PLAYER_CONFIG=str(tmp_path / "p.ini"),
               SIGNAGE_PLAYER_STATE_DIR=str(state), XDG_RUNTIME_DIR=str(tmp_path))
    launcher = os.path.join(here, "..", "bin", "signage-player-launch")
    for attempt in (1, 2, 3):
        res = subprocess.run(["bash", launcher], env=env, capture_output=True, text=True, timeout=60)
        assert res.returncode == 1
        # de uitvoer gaat nog steeds naar het logboek (stderr/stdout van de service)
        assert "wlroots backend" in res.stdout + res.stderr
        assert (state / "browser-failures").read_text().strip() == str(attempt)
    text = (state / "browser-last-failure.log").read_text()
    assert "poging 3 van 3" in text and "Could not activate session" in text and "Chromium 130.0" in text
    assert "Sessie:" in text and "DRM:" in text


def test_launcher_variants_and_capture_with_lingering_child(tmp_path):
    """cage breekt af (exitcode 134) terwijl de player-daemon nog even doorleeft: de uitvoer
    moet toch bewaard worden, en elke volgende start probeert een veiligere variant."""
    import subprocess
    here = os.path.dirname(os.path.abspath(__file__))
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "cage").write_text("#!/bin/bash\n( sleep 3 ) &\n"
                               "echo \"env: ${WLR_NO_HARDWARE_CURSORS:-x} ${WLR_DRM_NO_ATOMIC:-x} ${WLR_RENDERER:-x}\" >&2\n"
                               "echo 'wlr_output_init: Assertion failed.' >&2\nkill -ABRT $$\n")
    (fake / "chromium").write_text("#!/bin/bash\necho 'Chromium 155.0'\n")
    for f in fake.iterdir():
        f.chmod(0o755)
    (tmp_path / "p.ini").write_text("[player]\nrenderer = browser\n")
    state = tmp_path / "state"
    env = dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}", SIGNAGE_PLAYER_CONFIG=str(tmp_path / "p.ini"),
               SIGNAGE_PLAYER_STATE_DIR=str(state), XDG_RUNTIME_DIR=str(tmp_path))
    launcher = os.path.join(here, "..", "bin", "signage-player-launch")
    expected = ["x x x", "1 1 x", "1 1 pixman"]
    for attempt, want in enumerate(expected, 1):
        res = subprocess.run(["bash", launcher], env=env, capture_output=True, text=True, timeout=60)
        assert res.returncode == 134
        text = (state / "browser-last-failure.log").read_text()
        assert f"env: {want}" in text and "Assertion failed" in text, text
        assert "signaal 6" in text and f"poging {attempt} van 3" in text
    assert (state / "browser-variant").read_text().strip() == "2"

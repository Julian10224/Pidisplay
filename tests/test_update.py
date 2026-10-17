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


def _launcher_env(tmp_path, cage_script):
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    (fake / "cage").write_text(cage_script)
    (fake / "chromium").write_text("#!/bin/bash\necho 'Chromium 155.0'\n")
    for f in fake.iterdir():
        f.chmod(0o755)
    (tmp_path / "p.ini").write_text("[player]\nrenderer = browser\n")
    return dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}", SIGNAGE_PLAYER_CONFIG=str(tmp_path / "p.ini"),
                SIGNAGE_PLAYER_STATE_DIR=str(tmp_path / "state"), XDG_RUNTIME_DIR=str(tmp_path))


HERE_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")


def test_launcher_execs_cage_and_after_exit_counts_failures(tmp_path):
    """De launcher start cage als hoofdproces (exec, met de oorspronkelijke stdin); na een te
    snelle, mislukte stop telt signage-player-after-exit dat, kiest de volgende variant en
    schrijft de foutmelding."""
    import subprocess
    record = tmp_path / "cage-calls.txt"
    env = _launcher_env(tmp_path, "#!/bin/bash\n"
                        f"echo \"$$ ${{WLR_NO_HARDWARE_CURSORS:-x}} ${{WLR_DRM_NO_ATOMIC:-x}} ${{WLR_RENDERER:-x}} "
                        f"${{LIBC_FATAL_STDERR_:-x}} $*\" >> {record}\n"
                        f"echo \"runtime=$XDG_RUNTIME_DIR\" >> {record}\n"
                        "kill -ABRT $$\n")
    (tmp_path / "rt").mkdir()
    env.update(RUNTIME_DIRECTORY=str(tmp_path / "rt"), XDG_RUNTIME_DIR="/run/user/999")   # zoals PAM het zet
    state = tmp_path / "state"
    expected = ["x x x", "1 1 x", "1 1 pixman"]
    for attempt, want in enumerate(expected, 1):
        res = subprocess.run(["bash", os.path.join(HERE_BIN, "signage-player-launch")], env=env,
                             capture_output=True, text=True, timeout=60)
        assert res.returncode == -6, res.stderr          # het launcher-proces zelf werd cage (exec)
        calls = [c for c in record.read_text().strip().splitlines() if "--renderer browser" in c]
        pid, vars_ = calls[-1].split(" ", 1)
        assert vars_.startswith(want + " 1 -s"), calls[-1]          # varianten + LIBC_FATAL_STDERR_
        assert "--renderer browser" in vars_
        assert f"runtime={tmp_path / 'rt'}" in record.read_text(), "Wayland-socket in de eigen runtimemap"
        assert (state / "browser-start").exists()
        after = dict(env, SERVICE_RESULT="signal", EXIT_CODE="killed", EXIT_STATUS="ABRT")
        subprocess.run(["bash", os.path.join(HERE_BIN, "signage-player-after-exit")], env=after, check=True,
                       capture_output=True, text=True, timeout=60)
        assert (state / "browser-failures").read_text().strip() == str(attempt)
        text = (state / "browser-last-failure.log").read_text()
        assert f"poging {attempt} van 3" in text and "afgebroken" in text and "Chromium 155.0" in text
        assert not (state / "browser-start").exists()
    assert (state / "browser-variant").read_text().strip() == "2"


def test_after_exit_normal_stop_does_not_count(tmp_path):
    import subprocess
    state = tmp_path / "state"
    state.mkdir()
    (state / "browser-failures").write_text("2\n")
    import time as _t
    (state / "browser-start").write_text(f"{int(_t.time())} 1\n")
    env = dict(os.environ, SIGNAGE_PLAYER_STATE_DIR=str(state), SERVICE_RESULT="success", EXIT_CODE="exited",
               EXIT_STATUS="0")
    subprocess.run(["bash", os.path.join(HERE_BIN, "signage-player-after-exit")], env=env, check=True, timeout=30)
    assert (state / "browser-failures").read_text().strip() == "0"
    assert not (state / "browser-last-failure.log").exists()
    # zonder startbestand (mpv-weergave) gebeurt er niets
    subprocess.run(["bash", os.path.join(HERE_BIN, "signage-player-after-exit")], env=env, check=True, timeout=30)

"""Tests voor 1.4: opstartscherm (logo met laadbalk)."""

import os
import subprocess
import sys

from PIL import Image

from conftest import upload

HERE = os.path.dirname(os.path.abspath(__file__))
BOOTCONFIG = os.path.join(HERE, "..", "bin", "signage-bootconfig")
CMDLINE = "console=serial0,115200 console=tty1 root=PARTUUID=abcd-02 rootfstype=ext4 fsck.repair=yes rootwait cfg80211.ieee80211_regdom=NL\n"


def _bootconfig(tmp_path, mode, record):
    env = dict(os.environ)
    code = open(BOOTCONFIG).read().replace('RECORD = "/etc/signage/bootconfig-added"', f'RECORD = "{record}"')
    script = tmp_path / "bootconfig.py"
    script.write_text(code)
    return subprocess.run([sys.executable, str(script), mode, str(tmp_path / "boot")], capture_output=True,
                          text=True, env=env)


def test_bootconfig_enable_disable_roundtrip(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "cmdline.txt").write_text(CMDLINE)
    (boot / "config.txt").write_text("dtparam=audio=on\n[all]\ngpu_mem=128\n")
    record = tmp_path / "added"
    r = _bootconfig(tmp_path, "enable", record)
    assert r.returncode == 0 and r.stdout.strip() == "changed", r.stderr
    line = (boot / "cmdline.txt").read_text()
    assert line.count("\n") == 1 and line.startswith(CMDLINE.strip())
    for t in ("quiet", "splash", "plymouth.ignore-serial-consoles", "logo.nologo", "vt.global_cursor_default=0"):
        assert f" {t}" in line
    assert "disable_splash=1" in (boot / "config.txt").read_text()
    assert len(list(boot.glob("cmdline.txt.signage-backup-*"))) == 1
    # nog een keer: niets dubbel
    assert _bootconfig(tmp_path, "enable", record).stdout.strip() == "unchanged"
    assert (boot / "cmdline.txt").read_text() == line
    # terugdraaien: precies het origineel
    assert _bootconfig(tmp_path, "disable", record).stdout.strip() == "changed"
    assert (boot / "cmdline.txt").read_text() == CMDLINE
    assert (boot / "config.txt").read_text() == "dtparam=audio=on\n[all]\ngpu_mem=128\n"
    assert not record.exists()


def test_bootconfig_keeps_existing_quiet_and_refuses_odd_files(tmp_path):
    boot = tmp_path / "boot"
    boot.mkdir()
    (boot / "cmdline.txt").write_text(CMDLINE.strip() + " quiet\n")
    record = tmp_path / "added"
    assert _bootconfig(tmp_path, "enable", record).returncode == 0
    _bootconfig(tmp_path, "disable", record)
    assert (boot / "cmdline.txt").read_text() == CMDLINE.strip() + " quiet\n", "eigen 'quiet' moet blijven"
    (boot / "cmdline.txt").write_text("iets zonder root\n")
    r = _bootconfig(tmp_path, "enable", record)
    assert r.returncode == 1 and (boot / "cmdline.txt").read_text() == "iets zonder root\n"


def test_splash_api(client, media_files, tmp_path):
    r = client.get("/api/system/splash").json["splash"]
    assert r["logo"] is None
    default = client.get(r["preview_url"])
    assert default.status_code == 200 and default.mimetype == "image/png"
    big = tmp_path / "logo.png"
    Image.new("RGBA", (3000, 1000), (255, 0, 0, 128)).save(big)
    mid = upload(client, str(big)).json["media"]["id"]
    r = client.put("/api/system/splash", json={"media_id": mid}, headers=client.h)
    assert r.status_code == 200, r.json
    assert r.json["splash"]["logo"]["id"] == mid and "Opgeslagen" in r.json["message"]   # geen root-hulp in de test
    path = os.path.join(client.cfg.data_dir, "boot", "logo.png")
    with Image.open(path) as im:
        assert im.format == "PNG" and im.mode == "RGBA" and max(im.size) <= 1200
        assert im.getpixel((10, 10))[3] == 128, "transparantie blijft behouden"
    vid = upload(client, media_files["mp4"]).json["media"]["id"]
    assert client.put("/api/system/splash", json={"media_id": vid}, headers=client.h).status_code == 400
    assert client.put("/api/system/splash", json={"media_id": "f" * 32}, headers=client.h).status_code == 404
    assert client.put("/api/system/splash", json={"media_id": mid}).status_code == 403
    r = client.put("/api/system/splash", json={"media_id": None}, headers=client.h)
    assert r.status_code == 200 and r.json["splash"]["logo"] is None and not os.path.exists(path)

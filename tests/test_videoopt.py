"""Tests voor 1.5: video's automatisch geschikt maken voor de Pi 3B+ (met de echte ffmpeg)."""

import os
import subprocess

import pytest

from conftest import upload
from signage import media, videoopt

AUTH = {"Authorization": "Bearer local-test-token-0123456789"}


def _ff(path, *args):
    if not os.path.exists(path):
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args, path], check=True)
    return path


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")
    out = {}
    out["p60"] = _ff(str(d / "telefoon60.mp4"), "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=60",
                     "-f", "lavfi", "-i", "sine=f=440", "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest")
    out["ok"] = _ff(str(d / "goed.mp4"), "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=25", "-t", "2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p")
    out["hevc"] = _ff(str(d / "iphone.mov"), "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30", "-t", "2",
                      "-c:v", "libx265", "-x265-params", "log-level=error", "-tag:v", "hvc1")
    out["uhd"] = _ff(str(d / "4k.mp4"), "-f", "lavfi", "-i", "testsrc=size=3840x2160:rate=25", "-t", "1",
                     "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p")
    # staande telefoonvideo: 1920x1080 opgeslagen met 90° rotatie-metadata, 60 fps
    out["portrait"] = _ff(str(d / "staand.mp4"), "-display_rotation", "90", "-i", out["p60"], "-c", "copy")
    return out


def _probe_v(path):
    info = media.ffprobe(path)
    return [s for s in info["streams"] if s["codec_type"] == "video"][0], info


def test_analyse_reasons(clips):
    assert videoopt.analyse(media.ffprobe(clips["ok"])) == []
    assert videoopt.analyse(media.ffprobe(clips["p60"])) == ["60 fps"]
    assert videoopt.analyse(media.ffprobe(clips["hevc"])) == ["HEVC/H.265"]
    assert videoopt.analyse(media.ffprobe(clips["uhd"])) == ["3840x2160"]


def test_transcode_targets(clips, tmp_path):
    for name, (w, h, fps) in {"p60": (1920, 1080, 30), "hevc": (1920, 1080, 30), "uhd": (1920, 1080, 25)}.items():
        dst = str(tmp_path / f"{name}.mp4")
        seen = []
        assert videoopt.transcode(clips[name], dst, media.ffprobe(clips[name]), "libx264", progress=seen.append)
        v, info = _probe_v(dst)
        assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p" and v["profile"] == "High"
        assert (int(v["width"]), int(v["height"])) == (w, h)
        assert abs(media._fps(v) - fps) < 0.1
        assert videoopt.analyse(info) == [], "de afspeelversie zelf moet geschikt zijn"
        assert seen and seen[-1] == 1.0
    a = [s for s in media.ffprobe(str(tmp_path / "p60.mp4"))["streams"] if s["codec_type"] == "audio"]
    assert a and a[0]["codec_name"] == "aac", "geluid blijft behouden"


def test_portrait_phone_video_stays_portrait(clips, tmp_path):
    dst = str(tmp_path / "staand-uit.mp4")
    assert videoopt.analyse(media.ffprobe(clips["portrait"])) == ["60 fps"]
    videoopt.transcode(clips["portrait"], dst, media.ffprobe(clips["portrait"]), "libx264")
    v, _ = _probe_v(dst)
    assert (int(v["width"]), int(v["height"])) == (1080, 1920) and videoopt._rotation(v) == 0


def test_portrait_box_and_rotation_metadata():
    info = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": "30/1",
                         "side_data_list": [{"rotation": -90}]}], "format": {"duration": "2"}}
    assert videoopt._target(info)[:2] == (1080, 1920)          # staand blijft staand, volle resolutie
    info["streams"][0]["side_data_list"] = []
    info["streams"][0].update(width=2160, height=3840)
    assert videoopt._target(info)[:2] == (1080, 1920)


def test_optimizer_end_to_end(client, clips):
    opt = client.application.extensions["signage"].optimizer
    heavy = upload(client, clips["p60"]).json["media"]
    light = upload(client, clips["ok"]).json["media"]
    assert heavy["optimize"]["state"] == "queued" and heavy["optimized"] is False
    pid = client.post("/api/playlists", json={"name": "V"}, headers=client.h).json["id"]
    client.put(f"/api/playlists/{pid}", json={"items": [{"media_id": heavy["id"]}, {"media_id": light["id"]}]},
               headers=client.h)
    client.post(f"/api/playlists/{pid}/activate", json={}, headers=client.h)
    rev = client.get("/api/player/v1/config", headers=AUTH).json["revision"]
    for mid in (heavy["id"], light["id"]):
        opt.queue.remove(mid)
        opt.process(mid)
    by = {m["id"]: m for m in client.get("/api/media").json["media"]}
    h, lt = by[heavy["id"]], by[light["id"]]
    assert h["optimized"] and h["optimize"]["state"] == "optimized" and h["optimize"]["reasons"] == ["60 fps"]
    assert h["warning"] is None and h["status"] == "ok"
    assert not lt["optimized"] and lt["optimize"]["state"] == "not_needed"
    # de player krijgt de afspeelversie (nieuwe revisie), het origineel blijft downloadbaar
    cfg = client.get("/api/player/v1/config", headers=AUTH).json
    assert cfg["revision"] != rev
    item = [i for i in cfg["items"] if i["media_id"] == heavy["id"]][0]
    assert item["path"].endswith(heavy["id"] + ".mp4") and "/renditions/" in item["path"]
    v, _ = _probe_v(item["path"])
    assert abs(media._fps(v) - 30) < 0.1
    assert client.get(f"/api/media/{heavy['id']}/download").status_code == 200
    # al omgezet: opnieuw vragen kan niet; verwijderen ruimt alles op
    assert client.post(f"/api/media/{heavy['id']}/optimize", headers=client.h).status_code == 409
    assert client.delete(f"/api/media/{heavy['id']}?force=1", headers=client.h).status_code == 200
    assert not os.path.exists(item["path"]) and heavy["id"] not in opt.results


def test_failed_optimization_and_retry(client, clips, monkeypatch):
    opt = client.application.extensions["signage"].optimizer
    m = upload(client, clips["uhd"]).json["media"]
    opt.queue.remove(m["id"])

    def boom(*a, **k):
        raise RuntimeError("encoder kapot")
    monkeypatch.setattr(videoopt, "transcode", boom)
    opt.process(m["id"])
    got = client.get("/api/media").json["media"][0]
    assert got["optimize"]["state"] == "failed" and "encoder kapot" in got["optimize"]["message"]
    assert not got["optimized"] and got["warning"], "het origineel blijft spelen, met de waarschuwing"
    # opnieuw proberen via de website
    r = client.post(f"/api/media/{m['id']}/optimize", headers=client.h)
    assert r.status_code == 200 and r.json["media"]["optimize"]["state"] == "queued"
    # setting
    assert client.get("/api/settings").json["settings"]["optimize_videos"] is True
    r = client.put("/api/settings", json={"optimize_videos": False}, headers=client.h)
    assert r.json["settings"]["optimize_videos"] is False and opt.enabled() is False

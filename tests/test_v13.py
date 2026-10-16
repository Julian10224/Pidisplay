"""Tests voor 1.3: live bediening (vorige/volgende/naar item) vanuit de website.

- API: validatie, alleen voor online players met ondersteuning, long polling met epoch.
- mpv-weergave (nagebootste mpv): volgorde na opdrachten, overgang, willekeurig, 'blijft staan'.
- Van begin tot eind: website -> server -> player -> status terug in de website.
De browserweergave wordt getest in test_browser_player.py.
"""

import time

from conftest import make_config, upload
from test_player import (PlayerThread, fake_log, free_port, img, live_server, loads,  # noqa: F401
                         wait_for, write_cache)

AUTH = {"Authorization": "Bearer local-test-token-0123456789"}


def _beat(client, control=True, **status):
    body = {"state": "playing", "current": status.pop("current", None)}
    if control:
        body["control"] = {"v": 1, "last": status.pop("last", None)}
    body.update(status)
    assert client.post("/api/player/v1/heartbeat", json=body, headers=AUTH).status_code == 200


def _playlist(client, media_files):
    a = upload(client, media_files["png"]).json["media"]["id"]
    b = upload(client, media_files["mp4"]).json["media"]["id"]
    pid = client.post("/api/playlists", json={"name": "Live"}, headers=client.h).json["id"]
    pl = client.put(f"/api/playlists/{pid}", json={"items": [{"media_id": a}, {"media_id": b}]},
                    headers=client.h).json["playlist"]
    client.post(f"/api/playlists/{pid}/activate", json={}, headers=client.h)
    return pid, [it["item_id"] for it in pl["items"]]


# -- API ------------------------------------------------------------------------------------

def test_control_api_validation(client, media_files):
    pid, item_ids = _playlist(client, media_files)
    url = "/api/players/local/control"
    # offline
    r = client.post(url, json={"action": "next"}, headers=client.h)
    assert r.status_code == 409 and "niet online" in r.json["error"]
    # online, maar oude player zonder live bediening
    _beat(client, control=False)
    r = client.post(url, json={"action": "next"}, headers=client.h)
    assert r.status_code == 409 and "1.3" in r.json["error"]
    _beat(client)
    assert client.post(url, json={"action": "explode"}, headers=client.h).status_code == 400
    assert client.post(url, json={"action": "goto"}, headers=client.h).status_code == 400
    assert client.post(url, json={"action": "goto", "item_id": True}, headers=client.h).status_code == 400
    assert client.post(url, json={"action": "goto", "item_id": 999999}, headers=client.h).status_code == 409
    assert client.post("/api/players/bestaatniet/control", json={"action": "next"}, headers=client.h).status_code == 404
    # zonder CSRF-token of zonder inlog geweigerd
    assert client.post(url, json={"action": "next"}).status_code == 403
    r = client.post(url, json={"action": "goto", "item_id": item_ids[1], "transition": True}, headers=client.h)
    assert r.status_code == 200 and r.json["seq"] == 1
    # de configuratie voor de player bevat de item-id's
    cfg = client.get("/api/player/v1/config", headers=AUTH).json
    assert [it["item_id"] for it in cfg["items"]] == item_ids


def test_player_control_long_poll(client, media_files):
    _playlist(client, media_files)
    _beat(client)
    # eerste contact: alleen epoch en volgnummer, nooit oude opdrachten
    client.post("/api/players/local/control", json={"action": "next"}, headers=client.h)
    r = client.get("/api/player/v1/control?wait=0", headers=AUTH).json
    assert r["commands"] == [] and r["seq"] == 1 and r["epoch"]
    epoch = r["epoch"]
    # niets nieuws
    r = client.get(f"/api/player/v1/control?epoch={epoch}&after=1&wait=0", headers=AUTH).json
    assert r["commands"] == [] and r["seq"] == 1
    client.post("/api/players/local/control", json={"action": "prev"}, headers=client.h)
    client.post("/api/players/local/control", json={"action": "next", "transition": True}, headers=client.h)
    r = client.get(f"/api/player/v1/control?epoch={epoch}&after=1&wait=0", headers=AUTH).json
    assert [(c["seq"], c["action"], c["transition"]) for c in r["commands"]] == [(2, "prev", False), (3, "next", True)]
    assert all(0 <= c["age"] < 5 for c in r["commands"])
    # verkeerd epoch (server herstart): niets uitvoeren, wel het nieuwe volgnummer
    r = client.get("/api/player/v1/control?epoch=oud&after=0&wait=0", headers=AUTH).json
    assert r["commands"] == [] and r["seq"] == 3
    # verouderde opdrachten (ouder dan 15 s) worden niet meer geleverd
    from signage.app import state
    with client.application.app_context():
        for c in state().control["local"]["cmds"]:
            c["at"] -= 60
    r = client.get(f"/api/player/v1/control?epoch={epoch}&after=1&wait=0", headers=AUTH).json
    assert r["commands"] == []
    # zonder token geen toegang
    assert client.get("/api/player/v1/control").status_code == 401


def test_long_poll_wakes_up_on_command(live_server, media_files):
    import json
    import threading
    import urllib.request
    c, _player_ini, _srv, cfg = live_server
    _playlist(c, media_files)
    _beat(c)
    base = f"http://127.0.0.1:{cfg.port}/api/player/v1/control"

    def get(q):
        req = urllib.request.Request(base + q, headers=AUTH)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    first = get("?wait=0")
    out = {}

    def waiter():
        t0 = time.time()
        out["r"] = get(f"?epoch={first['epoch']}&after={first['seq']}&wait=20")
        out["t"] = time.time() - t0

    th = threading.Thread(target=waiter)
    th.start()
    time.sleep(1.0)
    c.post("/api/players/local/control", json={"action": "next"}, headers=c.h)
    th.join(25)
    assert out["r"]["commands"][0]["action"] == "next"
    assert 0.9 < out["t"] < 3, out["t"]      # wachtte, en werd direct gewekt


def test_heartbeat_control_field_is_sanitized(client):
    _beat(client, last={"seq": 4, "action": "next", "result": "x" * 1000, "evil": "y"})
    p = client.get("/api/players").json["players"][0]
    last = p["status"]["control"]["last"]
    assert set(last) == {"seq", "action", "result"} and len(last["result"]) == 200
    client.post("/api/player/v1/heartbeat", json={"state": "playing", "control": "rommel"}, headers=AUTH)
    assert client.get("/api/players").json["players"][0]["status"]["control"]["last"] is None


# -- mpv-weergave ------------------------------------------------------------------------------

def _mpv_items(tmp_path, n=4, duration=30, transition=None):
    names = [chr(ord("a") + i) for i in range(n)]
    return [{"media_id": str(i), "item_id": 100 + i, "name": nm, "kind": "image",
             "local": img(tmp_path, nm + ".png"), "duration": duration, "transition": transition}
            for i, nm in enumerate(names)]


def _send(pt, action, **kw):
    pt.player.commands.put({"action": action, "seq": int(time.time() * 1000), **kw})


def _last_load(fake_log):
    return (loads(fake_log) or [None])[-1]


def test_mpv_live_next_prev_goto(tmp_path, fake_log):
    _ini, player_ini = make_config(tmp_path, port=free_port())
    write_cache(str(tmp_path / "player-cache"), _mpv_items(tmp_path))
    pt = PlayerThread(player_ini).start()
    try:
        assert wait_for(lambda: loads(fake_log) == ["a.png"])
        for action, kw, want in [("next", {}, "b.png"), ("next", {}, "c.png"), ("prev", {}, "b.png"),
                                 ("goto", {"item_id": 103}, "d.png"), ("next", {}, "a.png"), ("prev", {}, "d.png")]:
            n = len(loads(fake_log))
            _send(pt, action, **kw)
            assert wait_for(lambda: len(loads(fake_log)) > n, timeout=5), action
            assert _last_load(fake_log) == want, (action, loads(fake_log))
        st = pt.player.status()
        assert st["current"]["item_id"] == 103 and st["control"]["last"]["result"] == "ok"
        # onbekend item: niets gebeurt, wel een duidelijke uitslag
        n = len(loads(fake_log))
        _send(pt, "goto", item_id=555)
        assert wait_for(lambda: (pt.player.last_control or {}).get("action") == "goto"
                        and pt.player.last_control["result"] != "ok")
        time.sleep(0.5)
        assert len(loads(fake_log)) == n
        # na een opdracht loopt de playlist gewoon verder met de normale duur
        assert pt.player.deadline - time.time() > 20
    finally:
        pt.stop()


def test_mpv_live_with_and_without_transition(tmp_path, fake_log):
    _ini, player_ini = make_config(tmp_path, port=free_port())
    t = tmp_path / "overgang.mp4"
    t.write_bytes(b"x")
    write_cache(str(tmp_path / "player-cache"), _mpv_items(tmp_path, transition="T"),
                transitions={"T": {"media_id": "T", "name": "overgang", "local": str(t), "video_duration": 1.0}})
    pt = PlayerThread(player_ini).start()
    try:
        assert wait_for(lambda: loads(fake_log) == ["a.png"])
        _send(pt, "next", transition=True)
        assert wait_for(lambda: loads(fake_log) == ["a.png", "overgang.mp4", "b.png"], timeout=10), loads(fake_log)
        _send(pt, "goto", item_id=103)             # zonder overgang: direct
        assert wait_for(lambda: loads(fake_log)[-1] == "d.png", timeout=5)
        assert loads(fake_log) == ["a.png", "overgang.mp4", "b.png", "d.png"]
        # opdracht tijdens een overgang: overgang stopt, gekozen item verschijnt
        _send(pt, "next", transition=True)
        assert wait_for(lambda: pt.player.state == "PLAYING_TRANSITION", timeout=5)
        _send(pt, "next")
        assert wait_for(lambda: loads(fake_log)[-1] == "a.png" and pt.player.state == "PLAYING", timeout=5)
        assert pt.player.transition is None
    finally:
        pt.stop()


def test_mpv_live_shuffle_prev_and_hold(tmp_path, fake_log):
    _ini, player_ini = make_config(tmp_path, port=free_port())
    write_cache(str(tmp_path / "player-cache"), _mpv_items(tmp_path, n=5),
                playlist={"id": 1, "name": "T", "repeat_mode": "loop", "mute": False, "shuffle": True})
    pt = PlayerThread(player_ini).start()
    try:
        assert wait_for(lambda: len(loads(fake_log)) == 1)
        for _ in range(3):
            n = len(loads(fake_log))
            _send(pt, "next")
            assert wait_for(lambda: len(loads(fake_log)) > n)
        seen = loads(fake_log)
        assert len(set(seen)) == 4, seen          # binnen een ronde geen herhaling
        _send(pt, "prev")
        assert wait_for(lambda: len(loads(fake_log)) == 5)
        assert loads(fake_log)[-1] == seen[-2]
        _send(pt, "prev")
        assert wait_for(lambda: len(loads(fake_log)) == 6)
        assert loads(fake_log)[-1] == seen[-3]
    finally:
        pt.stop()

    open(fake_log, "w").close()
    tmp2 = tmp_path / "hold"
    tmp2.mkdir()
    _ini, player_ini = make_config(tmp2, port=free_port())
    write_cache(str(tmp2 / "player-cache"), _mpv_items(tmp2, n=2, duration=0.3),
                playlist={"id": 1, "name": "T", "repeat_mode": "once_hold_last", "mute": False})
    pt = PlayerThread(player_ini).start()
    try:
        assert wait_for(lambda: pt.player.state == "HOLD", timeout=10)
        _send(pt, "next")                          # vanuit 'blijft staan' weer naar het begin
        assert wait_for(lambda: loads(fake_log)[-1] == "a.png" and len(loads(fake_log)) == 3, timeout=5)
    finally:
        pt.stop()


# -- van begin tot eind ----------------------------------------------------------------------

def test_end_to_end_website_to_mpv_player(live_server, media_files, fake_log, tmp_path):
    c, player_ini, _srv, cfg = live_server
    ids = []
    for i, color in enumerate([(200, 0, 0), (0, 200, 0), (0, 0, 200)]):
        p = tmp_path / f"kleur{i}.png"
        from PIL import Image
        Image.new("RGB", (64, 64), color).save(p)
        ids.append(upload(c, str(p)).json["media"]["id"])
    pid = c.post("/api/playlists", json={"name": "E2E"}, headers=c.h).json["id"]
    items = c.put(f"/api/playlists/{pid}", json={"items": [{"media_id": m, "duration": 60} for m in ids]},
                  headers=c.h).json["playlist"]["items"]
    c.post(f"/api/playlists/{pid}/activate", json={}, headers=c.h)
    pt = PlayerThread(player_ini).start()

    def local():
        return c.get("/api/status").json["players"][0]

    try:
        assert wait_for(lambda: (local().get("status") or {}).get("control") and local()["online"], timeout=20)
        assert wait_for(lambda: [x.split(".")[0] for x in loads(fake_log)[-1:]] == [ids[0]], timeout=10)
        time.sleep(1.5)   # player wacht nu op opdrachten (long poll)
        t0 = time.time()
        r = c.post("/api/players/local/control", json={"action": "next"}, headers=c.h)
        assert r.status_code == 200, r.json
        assert wait_for(lambda: loads(fake_log)[-1].split(".")[0] == ids[1], timeout=5)
        on_screen = time.time() - t0
        # de website ziet het nieuwe item snel (directe statusmelding, niet pas na 15 s)
        assert wait_for(lambda: ((local()["status"] or {}).get("current") or {}).get("item_id") == items[1]["item_id"],
                        timeout=3)
        seen = time.time() - t0
        assert on_screen < 2.0 and seen < 3.0, (on_screen, seen)
        r = c.post("/api/players/local/control", json={"action": "goto", "item_id": items[2]["item_id"]}, headers=c.h)
        assert wait_for(lambda: loads(fake_log)[-1].split(".")[0] == ids[2], timeout=5)
        assert wait_for(lambda: ((local()["status"]["control"] or {}).get("last") or {}).get("seq") == str(r.json["seq"]),
                        timeout=3)
        assert local()["status"]["control"]["last"]["result"] == "ok"
        print(f"\n   opdracht -> op het scherm: {on_screen:.2f} s, zichtbaar in website: {seen:.2f} s")
    finally:
        pt.stop()


# -- lokale playerserver (browserweergave) -------------------------------------------------------

def test_local_command_box_and_status_origin(tmp_path):
    import json
    import threading
    import urllib.error
    import urllib.request

    from signage.player.browser import LocalServer, PageState
    port = free_port()
    got = []
    srv = LocalServer(port, PageState(port), got.append)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def cmd(q=""):
        return json.loads(urllib.request.urlopen(base + "/cmd" + q, timeout=30).read())

    try:
        srv.commands.put({"action": "next", "seq": 7, "age": 0})
        first = cmd()                                  # nieuwe pagina: geen oude opdrachten
        assert first["commands"] == [] and first["seq"] == 1
        t0 = time.time()
        threading.Timer(0.5, lambda: srv.commands.put({"action": "goto", "item_id": 3, "seq": 8, "age": 0.1})).start()
        r = cmd(f"?boot={first['boot']}&after=1")      # wacht en wordt gewekt
        assert 0.4 < time.time() - t0 < 3
        assert r["commands"] == [{"action": "goto", "transition": False, "item_id": 3, "id": 8}]
        assert cmd("?boot=anders&after=0")["commands"] == []   # daemon herstart: niets uitvoeren
        srv.commands.put({"action": "prev", "seq": 9, "age": 20})      # te oud
        assert cmd(f"?boot={first['boot']}&after=2")["commands"] == []
        # Status: de playerpagina mag posten, een HTML-pagina (andere origin) niet.
        ok = urllib.request.Request(base + "/status", data=b'{"phase": "PLAYING"}', method="POST",
                                    headers={"Origin": base, "Content-Type": "application/json"})
        assert urllib.request.urlopen(ok).status == 204 and got[-1] == {"phase": "PLAYING"}
        bad = urllib.request.Request(base + "/status", data=b'{"phase": "NEP"}', method="POST",
                                     headers={"Origin": f"http://localhost:{port}", "Content-Type": "text/plain"})
        try:
            urllib.request.urlopen(bad)
            assert False, "status vanaf HTML-content geaccepteerd"
        except urllib.error.HTTPError as e:
            assert e.code == 403
        assert got[-1] == {"phase": "PLAYING"}
    finally:
        srv.shutdown()


def test_mpv_live_next_keeps_shuffle_rounds(tmp_path, fake_log):
    """Met willekeurige volgorde blijft 'Volgende' willekeurig: volledige, geschudde rondes,
    ook als willekeurig pas tijdens het afspelen wordt aangezet."""
    _ini, player_ini = make_config(tmp_path, port=free_port())
    items = _mpv_items(tmp_path, n=6)
    names = [it["name"] + ".png" for it in items]
    write_cache(str(tmp_path / "player-cache"), items)
    pt = PlayerThread(player_ini).start()
    try:
        assert wait_for(lambda: len(loads(fake_log)) == 1)
        _send(pt, "next")
        assert wait_for(lambda: len(loads(fake_log)) == 2)
        # willekeurig aanzetten tijdens het afspelen (bewerking van dezelfde playlist)
        cfg = dict(pt.player.cfg, revision="r2",
                   playlist={"id": 1, "name": "T", "repeat_mode": "loop", "mute": False, "shuffle": True})
        pt.player.queue.put((cfg, []))
        time.sleep(0.5)
        for i in range(18):
            n = len(loads(fake_log))
            _send(pt, "next")
            assert wait_for(lambda: len(loads(fake_log)) > n)
        seq = loads(fake_log)[1:]          # vanaf het item waarop willekeurig aanging
        rounds = [seq[i:i + 6] for i in range(0, 18, 6)]
        assert all(sorted(r) == sorted(names) for r in rounds), rounds
        assert sum(r == names for r in rounds) <= 1, f"op volgorde van de playlist: {rounds}"
        assert pt.player.status()["current"]["shuffle"] is True
    finally:
        pt.stop()

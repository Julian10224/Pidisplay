"""Tests van de browserplayer in (headless) Chromium.

Wat dit wel test: de echte playerpagina en lokale playerserver, de toestandsmachine,
voorladen, overgangen, schaalmodi (via schermafbeeldingen en pixelmetingen), HTML-isolatie
en herstel. Wat dit NIET test: de GPU, hardwaredecodering en het fysieke scherm van de Pi.
Headless-Chromium speelt geen H.264 af; daarom gebruiken deze tests VP9/WebM.

Vereist: pip install playwright pillow, en een Chromium (PW_CHROMIUM of /opt/pw-browsers).
"""

import io
import json
import os
import socket
import subprocess
import sys
import time

import pytest

pw = pytest.importorskip("playwright.sync_api")
from PIL import Image  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MEDIA = os.path.join(HERE, ".media", "browser")
CHROMIUM = os.environ.get("PW_CHROMIUM", "/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell")
W, H = 1280, 720

if not os.path.exists(CHROMIUM):
    pytest.skip("geen Chromium voor browsertests", allow_module_level=True)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="module")
def media():
    if not os.path.exists(os.path.join(MEDIA, "logo.png")):
        subprocess.run([sys.executable, os.path.join(HERE, "browser", "make_media.py"), MEDIA], check=True)
    return MEDIA


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM,
                              args=["--no-proxy-server", "--autoplay-policy=no-user-gesture-required",
                                    "--site-per-process"])
        yield b
        b.close()


class Rig:
    def __init__(self, tmp_path, browser, media):
        self.media = media
        self.port = free_port()
        self.scenario = str(tmp_path / "scenario.json")
        self.rev = 0
        self.browser = browser
        self.proc = None
        self.page = None

    # -- scenario -------------------------------------------------------------
    def item(self, kind, name, scale="cover", duration=1, transition=None, design=None, logo=False, item_id=None):
        it = {"media_id": name.replace("/", "_").replace(".", "_"), "name": name, "kind": kind, "scale": scale,
              "item_id": item_id,
              "duration": None if kind == "video" else duration, "transition": transition,
              "logo": {"media_id": "logo", "local": os.path.join(self.media, "logo.png")} if logo else None}
        if kind == "html":
            path = os.path.join(self.media, "html", name)
            it["html"] = {"source": "upload", "local": path, "entry": "index.html",
                          "width": design[0] if design else None, "height": design[1] if design else None}
        else:
            it["local"] = os.path.join(self.media, name)
            it["video_duration"] = {"portrait.webm": 2, "landscape.webm": 1.5, "short.webm": 0.2}.get(name)
        return it

    def write(self, items, playlist_id=1, repeat="loop", transitions=None, rotation=0, scale_default="cover",
              shuffle=False):
        self.rev += 1
        cfg = {"revision": f"r{self.rev}", "playlist": {"id": playlist_id, "name": f"P{playlist_id}",
                                                       "repeat_mode": repeat, "mute": True, "shuffle": shuffle},
               "items": items, "fallback_items": [], "transitions": transitions or {},
               "scale_default": scale_default, "volume": 0, "player": {"rotation": rotation},
               "standby": {"local": os.path.join(self.media, "standby.png")}, "system_name": "Test"}
        tmp = self.scenario + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(cfg, fh)
        os.replace(tmp, self.scenario)
        if self.proc:
            time.sleep(0.5)   # harness ziet de wijziging binnen 0,2 s

    def transition(self, name="transition.webm"):
        return {"T": {"local": os.path.join(self.media, name), "name": "overgang", "video_duration": 1.2}}

    # -- uitvoeren --------------------------------------------------------------
    def start(self):
        self.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "browser", "harness.py"), str(self.port),
                                      self.scenario], stdout=subprocess.PIPE, stdin=subprocess.PIPE, text=True)
        assert self.proc.stdout.readline().strip() == "READY"
        self.page = self.browser.new_page(viewport={"width": W, "height": H})
        self.page.goto(f"http://127.0.0.1:{self.port}/?test=1")
        return self

    def stop(self):
        if self.page:
            self.page.close()
        if self.proc:
            self.proc.kill()
            self.proc.wait()

    def cmd(self, action, **kw):
        """Opdracht van de live bediening, zoals de Syncer die van de server doorgeeft."""
        self.seq = getattr(self, "seq", 0) + 1
        self.proc.stdin.write(json.dumps({"action": action, "seq": self.seq, "age": 0, **kw}) + "\n")
        self.proc.stdin.flush()
        return self.seq

    def js(self, expr):
        return self.page.evaluate(expr)

    def history(self):
        return self.js("window.__signage.history")

    def probe(self):
        return self.js("window.__signage.probe")

    def wait_playing(self, name, timeout=15, count=1):
        end = time.time() + timeout
        while time.time() < end:
            n = sum(1 for h in self.history() if h["state"] == "PLAYING" and h["name"] == name)
            if n >= count:
                return True
            time.sleep(0.1)
        raise AssertionError(f"'{name}' speelde niet (x{count}); historie: {self.history()[-12:]}")

    def shot(self, delay=0.4):
        time.sleep(delay)
        return Image.open(io.BytesIO(self.page.screenshot())).convert("RGB")


@pytest.fixture
def rig(tmp_path, browser, media):
    r = Rig(tmp_path, browser, media)
    yield r
    r.stop()


def near(px, rgb, tol=60):
    return all(abs(a - b) <= tol for a, b in zip(px, rgb))


BLACK, RED, BLUE, GREEN = (0, 0, 0), (220, 30, 30), (30, 60, 220), (30, 180, 60)
CORNERS = [(3, 3), (W - 4, 3), (3, H - 4), (W - 4, H - 4)]


# -- schaalmodi -------------------------------------------------------------------------

@pytest.mark.parametrize("kind,name", [("image", "portrait.png"), ("video", "portrait.webm")])
def test_scale_modes(rig, kind, name):
    rig.write([rig.item(kind, name, scale="cover", duration=30)], playlist_id=1)
    rig.start()
    rig.wait_playing(name)
    img = rig.shot()
    # Vullen: staand beeld op liggend scherm => volledig rood, banden weggesneden, geen zwart.
    assert all(near(img.getpixel(c), RED) for c in CORNERS), [img.getpixel(c) for c in CORNERS]
    assert near(img.getpixel((W // 2, H // 2)), RED)

    rig.write([rig.item(kind, name, scale="contain", duration=30)], playlist_id=2)
    rig.wait_playing(name, count=2)
    img = rig.shot()
    # Passend: zwarte zijkanten, banden boven en onder zichtbaar.
    assert all(near(img.getpixel(c), BLACK, 20) for c in CORNERS)
    assert near(img.getpixel((W // 2, 3)), BLUE) and near(img.getpixel((W // 2, H - 4)), GREEN)

    rig.write([rig.item(kind, name, scale="fill", duration=30)], playlist_id=3)
    rig.wait_playing(name, count=3)
    img = rig.shot()
    # Uitrekken: geen zwart, banden over de volle breedte (vervormd).
    assert near(img.getpixel((3, 3)), BLUE) and near(img.getpixel((W - 4, H - 4)), GREEN)
    assert near(img.getpixel((W // 2, H // 2)), RED)
    assert rig.probe()["blank"] == 0


def test_small_image_is_upscaled_to_fill(rig):
    rig.write([rig.item("image", "small.png", duration=30)])
    rig.start()
    rig.wait_playing("small.png")
    img = rig.shot()
    assert all(near(img.getpixel(c), (240, 220, 30)) for c in CORNERS)


def test_html_fills_viewport(rig):
    rig.write([rig.item("html", "page", duration=30)])
    rig.start()
    rig.wait_playing("page")
    img = rig.shot(1.0)
    assert all(near(img.getpixel(c), (250, 140, 20), 30) for c in CORNERS), [img.getpixel(c) for c in CORNERS]


@pytest.mark.parametrize("scale,corner", [("cover", (20, 120, 250)), ("contain", BLACK), ("fill", (20, 120, 250))])
def test_html_design_size_scaling(rig, scale, corner):
    rig.write([rig.item("html", "design", duration=30, scale=scale, design=(800, 800))])
    rig.start()
    rig.wait_playing("design")
    img = rig.shot(1.0)
    assert all(near(img.getpixel(c), corner, 30) for c in CORNERS), [img.getpixel(c) for c in CORNERS]
    assert near(img.getpixel((W // 2, H // 2)), (20, 120, 250), 30)


def test_rotation_90(rig):
    rig.write([rig.item("image", "portrait.png", duration=30)], rotation=90)
    rig.start()
    rig.wait_playing("portrait.png")
    img = rig.shot()
    # 90° rechtsom: de bovenkant (blauw) van het beeld staat rechts, de onderkant (groen) links.
    assert near(img.getpixel((W - 4, H // 2)), BLUE) and near(img.getpixel((3, H // 2)), GREEN)


# -- overgangen en wisselingen ----------------------------------------------------------

SEQ = [("image", "portrait.png"), ("image", "small.png"), ("video", "portrait.webm"), ("video", "landscape.webm"),
       ("html", "page"), ("html", "design"), ("image", "landscape.jpg"), ("html", "page"),
       ("video", "landscape.webm"), ("image", "portrait.png")]
# Dekt alle combinaties: foto→foto, foto→video, video→video, video→HTML, HTML→HTML, HTML→foto,
# foto→HTML, HTML→video, video→foto, en de terugkeer van het laatste naar het eerste item.


def _names(rig):
    return [h["name"] for h in rig.history() if h["state"] == "PLAYING"]


def _items(rig, transition):
    out = []
    for kind, name in SEQ:
        out.append(rig.item(kind, name, duration=1, transition=transition,
                            design=(800, 800) if name == "design" else None))
    return out


def test_all_switches_without_transitions(rig):
    rig.write(_items(rig, None))
    rig.start()
    rig.wait_playing("portrait.png", timeout=40, count=3)   # twee volledige rondes
    names = _names(rig)
    expected = [n for _k, n in SEQ]
    assert names[:len(expected)] == expected, names
    assert not any(h["state"] == "PLAYING_TRANSITION" for h in rig.history())
    p = rig.probe()
    assert p["blank"] == 0, p


def test_all_switches_with_transitions(rig):
    rig.write(_items(rig, "T"), transitions=rig.transition())
    rig.start()
    rig.wait_playing("portrait.png", timeout=60, count=3)
    hist = rig.history()
    names = _names(rig)
    expected = [n for _k, n in SEQ]
    assert names[:len(expected)] == expected, names
    # Tussen elke twee items precies één volledige overgang.
    states = [h["state"] for h in hist if h["state"] in ("PLAYING", "PLAYING_TRANSITION")]
    for a, b in zip(states, states[1:]):
        assert not (a == b == "PLAYING_TRANSITION"), "overgang twee keer achter elkaar"
        assert not (a == b == "PLAYING"), "item zonder overgang ertussen"
    # De overgang speelt volledig (1,2 s) voordat het volgende item begint.
    durations = []
    for i, h in enumerate(hist):
        if h["state"] == "PLAYING_TRANSITION":
            nxt = next(x for x in hist[i + 1:] if x["state"] == "STARTING_NEXT")
            durations.append((nxt["t"] - h["t"]) / 1000)
    assert len(durations) >= len(SEQ)
    assert min(durations) >= 1.0, durations
    p = rig.probe()
    assert p["blank"] == 0, p
    assert p.get("transitionHidden", 0) == 0 and p.get("transitionFrames", 0) > 0, p
    assert p["maxVideos"] <= 2, f"meer dan 2 video's tegelijk geladen: {p['maxVideos']}"


def test_missing_and_broken_transitions_are_skipped(rig, tmp_path):
    broken = tmp_path / "kapot.webm"
    broken.write_bytes(os.urandom(4000))
    trans = {"T": {"local": str(broken), "name": "kapot", "video_duration": 1},
             "M": {"local": str(tmp_path / "weg.webm"), "name": "weg", "video_duration": 1}}
    items = [rig.item("image", "portrait.png", transition="T"), rig.item("image", "small.png", transition="M")]
    rig.write(items, transitions=trans)
    rig.start()
    rig.wait_playing("portrait.png", timeout=20, count=3)
    errs = rig.js("window.__signage.errors")
    assert any("kapot" in e for e in errs) and any("weg" in e for e in errs), errs
    assert rig.probe()["blank"] == 0


def test_html_hang_does_not_block(rig):
    rig.write([rig.item("html", "hang", duration=2), rig.item("image", "small.png", duration=1)])
    rig.start()
    rig.wait_playing("hang")
    t0 = time.time()
    rig.wait_playing("small.png", timeout=10)
    assert time.time() - t0 < 6
    rig.wait_playing("hang", timeout=10, count=2)


def test_html_with_external_resources_offline(rig):
    rig.write([rig.item("html", "external", duration=1), rig.item("image", "small.png", duration=1)])
    rig.start()
    rig.wait_playing("small.png", timeout=15, count=2)
    assert _names(rig)[:2] == ["external", "small.png"]


def test_edit_during_transition_applies_after_it(rig):
    items = [rig.item("image", "portrait.png", duration=1, transition="T"),
             rig.item("image", "portrait.png", duration=1, transition="T")]
    rig.write(items, transitions=rig.transition())
    rig.start()
    end = time.time() + 15
    while rig.js("window.__signage.state()") != "PLAYING_TRANSITION":
        assert time.time() < end
        time.sleep(0.05)
    # Zelfde playlist bewerkt terwijl de overgang loopt.
    rig.write([rig.item("image", "landscape.jpg", duration=1, transition="T"),
               rig.item("image", "small.png", duration=1, transition="T")], transitions=rig.transition())
    rig.wait_playing("landscape.jpg", timeout=10)
    hist = rig.history()
    idx = max(i for i, h in enumerate(hist) if h["state"] == "PLAYING_TRANSITION" and
              any(x["state"] == "PLAYING" and x["name"] == "landscape.jpg" for x in hist[i:]))
    tr = hist[idx]
    nxt = next(x for x in hist[idx + 1:] if x["state"] == "STARTING_NEXT")
    assert (nxt["t"] - tr["t"]) / 1000 >= 1.0, "overgang niet onderbroken"
    assert rig.probe()["blank"] == 0


def test_other_playlist_switches_immediately(rig):
    rig.write([rig.item("image", "portrait.png", duration=30)], playlist_id=1)
    rig.start()
    rig.wait_playing("portrait.png")
    t0 = time.time()
    rig.write([rig.item("image", "small.png", duration=30)], playlist_id=2)
    rig.wait_playing("small.png", timeout=6)
    assert time.time() - t0 < 5
    assert rig.probe()["blank"] == 0


def test_empty_playlist_shows_standby_and_recovers(rig):
    rig.write([], playlist_id=1)
    rig.start()
    end = time.time() + 10
    while rig.js("window.__signage.state()") != "STANDBY":
        assert time.time() < end
        time.sleep(0.1)
    rig.write([rig.item("image", "small.png", duration=30)], playlist_id=1)
    rig.wait_playing("small.png", timeout=8)


def test_status_reports_viewport(rig):
    rig.write([rig.item("image", "small.png", duration=30)])
    rig.start()
    rig.wait_playing("small.png")
    snap = rig.js("window.__signage.snapshot()")
    assert snap["viewport"]["w"] == W and snap["viewport"]["h"] == H
    assert snap["current"]["name"] == "small.png" and snap["phase"] == "PLAYING"


def test_very_short_video_does_not_stall(rig):
    rig.write([rig.item("video", "short.webm"), rig.item("image", "small.png", duration=1)])
    rig.start()
    t0 = time.time()
    rig.wait_playing("small.png", timeout=15, count=3)
    assert time.time() - t0 < 10, "korte video wachtte op de time-out"
    assert rig.probe()["blank"] == 0


def test_html_is_not_preloaded_too_early(rig):
    rig.write([rig.item("image", "small.png", duration=8), rig.item("html", "page", duration=2)])
    rig.start()
    rig.wait_playing("small.png")
    time.sleep(1.5)
    assert rig.js("document.querySelectorAll('#stage iframe').length") == 0, "HTML al geladen terwijl er nog 6 s te gaan was"
    rig.wait_playing("page", timeout=15)
    assert rig.probe()["blank"] == 0


# -- 1.2: willekeurige volgorde en logo ------------------------------------------------------

def test_shuffle_rounds(rig):
    names = ["portrait.png", "small.png", "landscape.jpg", "standby.png", "logo.png"]
    rig.write([rig.item("image", n, duration=0.4) for n in names], shuffle=True)
    rig.start()
    rig.wait_playing(names[0], timeout=60, count=4)
    seq = _names(rig)
    rounds = [seq[i:i + 5] for i in range(0, len(seq) - 4, 5)][:3]
    assert len(rounds) == 3
    assert all(sorted(r) == sorted(names) for r in rounds), rounds
    assert any(r != names for r in rounds), "nooit geschud"
    for a, b in zip(rounds, rounds[1:]):
        assert a[-1] != b[0]
    assert rig.probe()["blank"] == 0


def test_shuffle_off_keeps_order(rig):
    names = ["portrait.png", "small.png", "landscape.jpg"]
    rig.write([rig.item("image", n, duration=0.4) for n in names], shuffle=False)
    rig.start()
    rig.wait_playing(names[0], timeout=30, count=3)
    assert _names(rig)[:6] == names * 2


YELLOW = (255, 255, 0)


def test_logo_top_left_per_item(rig):
    items = [rig.item("image", "portrait.png", duration=30, logo=True)]
    rig.write(items)
    rig.start()
    rig.wait_playing("portrait.png")
    img = rig.shot()
    m = round(0.03 * min(W, H))
    assert near(img.getpixel((m + 10, m + 10)), YELLOW, 30), img.getpixel((m + 10, m + 10))
    # onderste helft van het logo is transparant: daar is het item (rood) zichtbaar
    lw = min(400 * 0.18 * H / 200, W * 0.18)       # max 18% breed/hoog, proportioneel
    assert near(img.getpixel((m + 10, m + int(lw / 2 * 0.75))), RED), "transparantie"
    assert near(img.getpixel((m + int(lw) + 15, m + 5)), RED), "logo groter dan 18%"
    # zonder logo
    rig.write([rig.item("image", "portrait.png", duration=30)], playlist_id=2)
    rig.wait_playing("portrait.png", count=2)
    img = rig.shot()
    assert near(img.getpixel((m + 10, m + 10)), RED)


def test_logo_on_video_and_html_not_on_transition(rig):
    items = [rig.item("video", "landscape.webm", logo=True, transition="T"),
             rig.item("html", "page", duration=1.5, logo=True, transition="T"),
             rig.item("image", "portrait.png", duration=1, transition="T")]
    rig.write(items, transitions=rig.transition())
    rig.start()
    m = round(0.03 * min(W, H))
    seen = {}
    end = time.time() + 30
    while time.time() < end and len(seen) < 4:
        st = rig.js("window.__signage.state()")
        cur = rig.js("(window.__signage.snapshot().current||{}).name")
        key = "T" if st == "PLAYING_TRANSITION" else (cur if st == "PLAYING" else None)
        if key and key not in seen:
            time.sleep(0.3)
            if rig.js("window.__signage.state()") == st:
                seen[key] = rig.shot(0).getpixel((m + 10, m + 10))
        time.sleep(0.05)
    assert near(seen["landscape.webm"], YELLOW, 40), seen
    assert near(seen["page"], YELLOW, 40), seen
    assert not near(seen["T"], YELLOW, 60), seen
    assert near(seen["portrait.png"], RED), seen       # item zonder logo
    assert rig.probe()["blank"] == 0


def test_missing_logo_does_not_block_item(rig):
    it = rig.item("image", "small.png", duration=1)
    it["logo"] = {"media_id": "x", "local": os.path.join(rig.media, "bestaat-niet.png")}
    rig.write([it, rig.item("image", "portrait.png", duration=1)])
    rig.start()
    rig.wait_playing("small.png", timeout=15, count=2)


# -- 1.3: live bediening ---------------------------------------------------------------------

LIVE = ["portrait.png", "small.png", "landscape.jpg", "standby.png"]


def _live_items(rig, duration=30, transition=None):
    return [rig.item("image", n, duration=duration, transition=transition, item_id=100 + i) for i, n in enumerate(LIVE)]


def _wait_current(rig, name, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if rig.js("window.__signage.state()") == "PLAYING" and _names(rig)[-1:] == [name]:
            return True
        time.sleep(0.05)
    raise AssertionError(f"'{name}' niet op het scherm; historie: {rig.history()[-8:]}")


def test_live_next_prev_goto(rig):
    rig.write(_live_items(rig))
    rig.start()
    rig.wait_playing(LIVE[0])
    time.sleep(0.5)
    t0 = time.time()
    rig.cmd("next")
    _wait_current(rig, LIVE[1])
    delay = time.time() - t0
    for action, kw, want in [("next", {}, LIVE[2]), ("prev", {}, LIVE[1]), ("goto", {"item_id": 103}, LIVE[3]),
                             ("next", {}, LIVE[0]), ("prev", {}, LIVE[3])]:
        rig.cmd(action, **kw)
        _wait_current(rig, want)
    snap = rig.js("window.__signage.snapshot()")
    assert snap["current"]["item_id"] == 103 and snap["control"]["last"]["result"] == "ok"
    assert snap["control"]["last"]["seq"] == rig.seq
    assert _names(rig) == [LIVE[0], LIVE[1], LIVE[2], LIVE[1], LIVE[3], LIVE[0], LIVE[3]]
    assert rig.probe()["blank"] == 0, "zwart beeld tijdens doorklikken"
    assert delay < 1.5, delay
    # onbekend item: niets verandert, wel een uitslag
    rig.cmd("goto", item_id=999)
    time.sleep(0.8)
    snap = rig.js("window.__signage.snapshot()")
    assert snap["control"]["last"]["result"] != "ok" and snap["current"]["item_id"] == 103


def test_live_rapid_clicking_ends_on_right_item(rig):
    rig.write(_live_items(rig))
    rig.start()
    rig.wait_playing(LIVE[0])
    for _ in range(7):          # sneller klikken dan items kunnen laden
        rig.cmd("next")
        time.sleep(0.03)
    time.sleep(1.5)
    assert _names(rig)[-1] == LIVE[7 % len(LIVE)]
    assert rig.js("window.__signage.state()") == "PLAYING"
    assert rig.js("document.querySelectorAll('.slot').length") <= 3, "lagen blijven achter"
    assert rig.probe()["blank"] == 0


def test_live_with_transition_and_during_transition(rig):
    rig.write(_live_items(rig, transition="T"), transitions=rig.transition())
    rig.start()
    rig.wait_playing(LIVE[0])
    rig.cmd("next", transition=True)
    _wait_current(rig, LIVE[1], timeout=8)
    states = [h["state"] for h in rig.history() if h["state"] in ("PLAYING", "PLAYING_TRANSITION")]
    assert states == ["PLAYING", "PLAYING_TRANSITION", "PLAYING"], states
    rig.cmd("next")                                   # zonder overgang: direct
    _wait_current(rig, LIVE[2])
    states = [h["state"] for h in rig.history() if h["state"] in ("PLAYING", "PLAYING_TRANSITION")]
    assert states[-2:] == ["PLAYING", "PLAYING"], states
    # tijdens een overgang: de overgang stopt en het volgende item verschijnt
    rig.cmd("next", transition=True)
    end = time.time() + 5
    while rig.js("window.__signage.state()") != "PLAYING_TRANSITION" and time.time() < end:
        time.sleep(0.02)
    rig.cmd("next")
    _wait_current(rig, LIVE[3])
    time.sleep(0.3)
    on = rig.js("Array.from(document.querySelectorAll('.slot.on video')).length")
    assert on == 0, "overgangsvideo bleef zichtbaar"
    assert rig.probe()["blank"] == 0


def test_live_shuffle_prev_and_hold(rig):
    rig.write(_live_items(rig), shuffle=True)
    rig.start()
    end = time.time() + 15
    while not _names(rig) and time.time() < end:
        time.sleep(0.1)
    for i in range(3):
        rig.cmd("next")
        end = time.time() + 5
        while len(_names(rig)) < i + 2 and time.time() < end:
            time.sleep(0.05)
    seen = _names(rig)
    assert len(seen) == 4 and len(set(seen)) == 4, seen
    rig.cmd("prev")
    _wait_current(rig, seen[-2])
    rig.cmd("prev")
    _wait_current(rig, seen[-3])


def test_live_hold_next_restarts(rig):
    rig.write(_live_items(rig, duration=0.3)[:2], repeat="once_hold_last")
    rig.start()
    end = time.time() + 10
    while rig.js("window.__signage.state()") != "HOLD" and time.time() < end:
        time.sleep(0.1)
    rig.cmd("next")
    _wait_current(rig, LIVE[0])


def test_live_old_commands_not_replayed(rig):
    rig.write(_live_items(rig))
    rig.start()
    rig.wait_playing(LIVE[0])
    rig.cmd("next")
    _wait_current(rig, LIVE[1])
    # pagina opnieuw laden (bijv. Chromium herstart): de oude opdracht wordt niet nog eens uitgevoerd
    rig.page.reload()
    rig.wait_playing(LIVE[0])
    time.sleep(1.5)
    assert _names(rig) == [LIVE[0]]
    rig.cmd("next")
    _wait_current(rig, LIVE[1])


def test_page_status_reaches_daemon_after_command(rig):
    """De playerpagina meldt haar status (met Origin-header) aan de daemon, direct na een opdracht."""
    rig.write(_live_items(rig))
    rig.start()
    rig.wait_playing(LIVE[0])
    seq = rig.cmd("goto", item_id=102)
    _wait_current(rig, LIVE[2])
    end = time.time() + 2
    st = {}
    while time.time() < end:
        try:
            st = json.load(open(rig.scenario + ".status"))
        except (OSError, ValueError):
            st = {}
        if (((st.get("control") or {}).get("last") or {}).get("seq") == seq
                and (st.get("current") or {}).get("item_id") == 102):
            break
        time.sleep(0.05)
    assert st["control"]["last"] == {"seq": seq, "action": "goto", "result": "ok"}, st.get("control")
    assert st["current"]["item_id"] == 102 and st["phase"] == "PLAYING"


def test_live_control_end_to_end_browser_renderer(tmp_path, browser):
    """Website -> server -> Syncer -> BrowserRenderer (daemon) -> playerpagina -> status terug."""
    import threading

    from conftest import login, make_config, upload
    from waitress import create_server

    from signage.app import create_app
    from signage.config import Config, read_player_config
    from signage.player.browser import BrowserRenderer

    port, local_port = free_port(), free_port()
    ini, player_ini = make_config(tmp_path, port=port)
    with open(player_ini, "a") as fh:
        fh.write(f"local_port = {local_port}\n")
    cfg = Config(ini)
    app = create_app(cfg, start_background=False)
    srv = create_server(app, host="127.0.0.1", port=port, threads=6)
    threading.Thread(target=srv.run, daemon=True).start()
    c = app.test_client()
    h = c.h = login(c, cfg)
    ids = []
    for i, col in enumerate([(200, 30, 30), (30, 180, 60), (30, 60, 220)]):
        p = tmp_path / f"k{i}.png"
        Image.new("RGB", (640, 360), col).save(p)
        ids.append(upload(c, str(p)).json["media"]["id"])
    pid = c.post("/api/playlists", json={"name": "B"}, headers=h).json["id"]
    items = c.put(f"/api/playlists/{pid}", json={"items": [{"media_id": m, "duration": 60} for m in ids]},
                  headers=h).json["playlist"]["items"]
    c.post(f"/api/playlists/{pid}/activate", json={}, headers=h)

    r = BrowserRenderer(read_player_config(player_ini), launch_browser=False)
    t = threading.Thread(target=r.run, daemon=True)
    import signal as _signal
    orig = _signal.signal
    _signal.signal = lambda *a: None      # run() installeert signaalhandlers; niet in een thread
    try:
        t.start()
        time.sleep(0.3)
    finally:
        _signal.signal = orig
    page = browser.new_page(viewport={"width": W, "height": H})

    def local():
        return c.get("/api/status").json["players"][0]

    def cur():
        return ((local().get("status") or {}).get("current") or {}).get("item_id")

    def wait(cond, timeout):
        end = time.time() + timeout
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.05)
        return False

    try:
        assert wait(lambda: r.cfg is not None, 15)
        page.goto(f"http://127.0.0.1:{local_port}/?test=1")
        assert wait(lambda: cur() == items[0]["item_id"], 20), local().get("status")
        time.sleep(1.0)
        timings = []
        for body, want in [({"action": "next"}, 1), ({"action": "goto", "item_id": items[0]["item_id"]}, 0),
                           ({"action": "prev"}, 2)]:
            t0 = time.time()
            assert c.post("/api/players/local/control", json=body, headers=h).status_code == 200
            assert wait(lambda: cur() == items[want]["item_id"], 5), (body, local()["status"])
            timings.append(time.time() - t0)
        assert local()["status"]["control"]["last"]["result"] == "ok"
        assert page.evaluate("window.__signage.probe").get("blank") == 0
        assert max(timings) < 2.0, timings
    finally:
        r.running = False
        page.close()
        t.join(5)
        srv.close()


def test_live_next_keeps_shuffle_rounds(rig):
    names = ["portrait.png", "small.png", "landscape.jpg", "standby.png", "logo.png"]
    rig.write([rig.item("image", n, duration=30, item_id=100 + i) for i, n in enumerate(names)], shuffle=True)
    rig.start()
    end = time.time() + 10
    while not _names(rig) and time.time() < end:
        time.sleep(0.1)
    for _ in range(14):
        n = len(_names(rig))
        rig.cmd("next")
        e = time.time() + 5
        while len(_names(rig)) <= n and time.time() < e:
            time.sleep(0.03)
    seq = _names(rig)
    rounds = [seq[i:i + 5] for i in range(0, 15, 5)]
    assert all(sorted(r) == sorted(names) for r in rounds), rounds
    assert sum(r == names for r in rounds) <= 1, rounds
    assert rig.js("window.__signage.snapshot()")["current"]["shuffle"] is True
    assert rig.probe()["blank"] == 0


def test_never_more_than_two_videos_loaded(rig):
    """De Pi 3B+ decodeert hooguit 2 video's tegelijk in hardware: video → overgang → video
    mag nooit 3 geladen video's geven, en er mag geen zwart beeld ontstaan."""
    items = [rig.item("video", "portrait.webm", transition="T"), rig.item("video", "landscape.webm", transition="T"),
             rig.item("image", "small.png", duration=1, transition="T"), rig.item("video", "short.webm", transition="T")]
    rig.write(items, transitions=rig.transition())
    rig.start()
    rig.wait_playing("portrait.webm", timeout=60, count=2)
    p = rig.probe()
    assert p["maxVideos"] <= 2, p
    assert p["blank"] == 0, p
    names = _names(rig)
    assert names[:4] == ["portrait.webm", "landscape.webm", "small.png", "short.webm"], names
    trans = [h for h in rig.history() if h["state"] == "PLAYING_TRANSITION"]
    assert len(trans) >= 4, "elke overgang speelde"


def test_shuffle_mixed_always_transition(rig):
    """Willekeurige volgorde met video's, een HTML-pagina en een foto, elk met overgang: vóór
    élk item (ook het eerste item van de playlist en bij het begin van een nieuwe ronde) speelt
    de overgang."""
    items = [rig.item("video", "landscape.webm", transition="T", item_id=1),
             rig.item("html", "page", duration=1.5, transition="T", item_id=2),
             rig.item("image", "small.png", duration=1, transition="T", item_id=3),
             rig.item("video", "short.webm", transition="T", item_id=4)]
    rig.write(items, transitions=rig.transition(), shuffle=True)
    rig.start()
    rig.wait_playing("landscape.webm", timeout=90, count=4)
    states = [(h["state"], h["name"]) for h in rig.history() if h["state"] in ("PLAYING", "PLAYING_TRANSITION")]
    plays = [n for s, n in states if s == "PLAYING"]
    assert len(plays) >= 9, states
    for a, b in zip(states, states[1:]):
        assert not (a[0] == b[0] == "PLAYING"), f"'{b[1]}' zonder overgang na '{a[1]}': {states}"
    p = rig.probe()
    assert p["blank"] == 0 and p["maxVideos"] <= 2, p


def test_shuffle_round_survives_edit_and_restart(rig):
    """Eerlijke willekeurige volgorde: een bewerking van de playlist of een herstart van de
    player begint geen nieuwe ronde; eerst komen de items die in deze ronde nog niet speelden."""
    names = ["portrait.png", "small.png", "landscape.jpg", "standby.png", "logo.png"]
    items = [rig.item("image", n, duration=2, item_id=10 + i) for i, n in enumerate(names)]
    rig.write(items, shuffle=True)
    rig.start()

    def wait_count(n, timeout=30):
        end = time.time() + timeout
        while len(_names(rig)) < n and time.time() < end:
            time.sleep(0.05)
        assert len(_names(rig)) >= n, _names(rig)

    def done():
        return rig.js("JSON.parse(localStorage.getItem('signage.shuffleRound')).done")

    wait_count(2)
    first = _names(rig)[:2]
    rig.write(list(reversed(items)), shuffle=True)        # bewerkt (andere volgorde), zelfde playlist
    wait_count(3)
    seen = _names(rig)[:3]
    assert len(set(seen)) == 3, seen                      # na de bewerking geen herhaling
    played_keys = done()
    assert len(played_keys) == 3, played_keys
    rig.page.reload()
    end = time.time() + 30
    while len(_names(rig)) < 3 and time.time() < end:
        time.sleep(0.05)
    after = _names(rig)
    rest = sorted(set(names) - set(seen))
    assert sorted(after[:2]) == rest, (seen, after)       # eerst de rest van de ronde
    assert len(set(after[2:7])) == 5 or len(after) < 7    # daarna een volledige nieuwe ronde
    assert first == seen[:2]

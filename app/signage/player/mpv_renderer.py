"""mpv-weergave: speelt foto's, video's en overgangen direct op het scherm (DRM/KMS).

Wordt gebruikt als terugvaloptie als de browserweergave niet beschikbaar is, of als
'renderer = mpv' is ingesteld. HTML-pagina's kan mpv niet tonen; die worden overgeslagen
(met een melding in het dashboard).

Toestandsmachine (zie STATES):
  STARTING ─▶ LOADING_NEXT ─▶ PLAYING ─(klaar)─▶ PREPARE_TRANSITION ─▶ PLAYING_TRANSITION
                   ▲                 │                                       │ (afgelopen of fout)
                   └─────────────────┴──────────(geen overgang)──────────────┘
  PLAYING ─(laatste item, 'eenmaal en laten staan')─▶ HOLD
  elk ─(niets afspeelbaar)─▶ STANDBY ─(30 s / nieuwe config)─▶ LOADING_NEXT
  ERROR = mpv start niet; herstel met afkoelperiode.

Een overgang wordt alleen gestart vanuit PLAYING (nooit na een overgang), zodat hij nooit
twee keer achter elkaar of uit zichzelf speelt.

Willekeurige volgorde: bij elke nieuwe ronde wordt de lijst geschud (elk item één keer, het
eerste item nooit gelijk aan het vorige). Logo's worden als mpv-overlay linksboven getoond
(mpv 'overlay-add'); tijdens een overgang en op het standby-scherm is er geen logo.

Live bediening (vanuit de website): 'next', 'prev' en 'goto' worden in de hoofdlus
uitgevoerd, dus nooit tegelijk met een andere toestandswissel. Het gekozen item wordt als
'forced' doelwit klaargezet en via de normale weg (go_next, eventueel na de overgang)
geladen; daarna loopt de playlist gewoon verder vanaf dat item.
"""

import hashlib
import json
import logging
import os
import queue
import random
import signal
import threading
import time

from .. import __version__
from .mpv import Mpv, MpvError
from .sync import Syncer, display_info, load_cached_config, sd_notify

log = logging.getLogger("signage.player")

STATES = ("STARTING", "LOADING_NEXT", "PLAYING", "PREPARE_TRANSITION", "PLAYING_TRANSITION",
          "HOLD", "STANDBY", "ERROR")

SCALE_PROPS = {   # schaalmodus -> mpv-eigenschappen
    "cover": {"keepaspect": True, "panscan": 1.0},     # vullen: proportioneel, bijsnijden
    "contain": {"keepaspect": True, "panscan": 0.0},   # passend: alles zichtbaar, randen mogelijk
    "fill": {"keepaspect": False, "panscan": 0.0},     # uitrekken: vervormen
}


class MpvRenderer:
    PING_INTERVAL = 2.0
    FAIL_IDLE_AFTER = 3.0        # mpv idle zo lang na loadfile => bestand mislukt
    STANDBY_RETRY = 30.0
    MAX_RESTARTS = 5
    RESTART_WINDOW = 600.0
    COOLDOWN = 60.0

    def __init__(self, pcfg, note=None):
        self.pcfg = pcfg
        self.cache_dir = pcfg.get("cache_dir")
        os.makedirs(self.cache_dir, exist_ok=True)
        runtime = os.environ.get("RUNTIME_DIRECTORY") or self.cache_dir
        self.mpv = Mpv(pcfg.get("mpv_binary"), os.path.join(runtime, "mpv.sock"),
                       hwdec=pcfg.get("hwdec"), vo=pcfg.get("vo"), gpu_context=pcfg.get("gpu_context"),
                       audio_device=pcfg.get("audio_device"), extra_args=pcfg.get("extra_args"),
                       render_quality=(pcfg.get("render_quality") or "fast").strip().lower())
        self.queue = queue.Queue()
        self.cfg = None
        self.pending = None
        self.items = []
        self.state = "STARTING"
        self.fallback_active = False
        self.index = -1
        self.loaded_at = 0.0
        self.deadline = None
        self.current = None          # huidig playlistitem (ook tijdens de overgang erna)
        self.transition = None       # overgang die nu speelt
        self.consecutive_failures = 0
        self.standby_until = 0.0
        self.standby_reason = None
        self.restarts = []
        self.total_restarts = 0
        self.cooldown_until = 0.0
        self.next_start_at = 0.0
        self.errors = []
        self.warned = set()
        self.message = None
        self.note = note
        self.started = time.time()
        self.last_ping = 0.0
        self.lock = threading.Lock()
        self.running = True
        self.applied = {}
        self.mpv_started_once = False
        self.overlay = None          # huidige logo-overlay (of None)
        self.screen_size = None
        self.history = []            # (tijd, toestand, naam) voor tests en diagnose
        self.commands = queue.Queue()  # opdrachten van de live bediening
        self.forced = None           # (lijst, index, fallback) dat als volgende moet spelen
        self.played = []             # afgespeelde posities, voor 'vorige' bij willekeurige volgorde
        self.last_control = None
        self.video_loaded = False     # speelt er nu een video (voor de framemeting)?
        self.video_stats = {"dropped": 0, "total": 0}
        self.syncer = Syncer(pcfg, self.cache_dir, self.queue, self.status, on_command=self.commands.put)
        # Willekeurige volgorde: welke items in de lopende ronde al speelden (ook na een herstart
        # van de player of een bewerking van de playlist), zodat elk item eerst één keer langskomt.
        self.round_file = os.path.join(self.cache_dir, "shuffle-round.json")
        self.round = self._load_round()

    # Compatibiliteit met 1.0 (tests en logboeken gebruiken 'mode').
    @property
    def mode(self):
        return {"HOLD": "hold", "STANDBY": "standby", "ERROR": "error"}.get(
            self.state, "fallback" if self.fallback_active else "playing")

    # -- status ------------------------------------------------------------------
    def error(self, text, once_key=None):
        if once_key is not None:
            if once_key in self.warned:
                return
            self.warned.add(once_key)
        log.warning(text)
        with self.lock:
            self.errors.append(time.strftime("%H:%M:%S ") + text)
            self.errors = self.errors[-10:]

    def set_state(self, state, name=None):
        self.state = state
        self.history.append((time.time(), state, name))
        self.history = self.history[-200:]
        if state in ("PLAYING", "PLAYING_TRANSITION", "HOLD", "STANDBY") and hasattr(self, "syncer"):
            self.syncer.kick()   # website direct laten zien wat er op het scherm staat

    def status(self):
        with self.lock:
            cur = self.current
            playlist = (self.cfg or {}).get("playlist") or {}
            hw = self.applied.get("hwdec-current")
            showing = cur is not None and self.state in ("PLAYING", "HOLD", "LOADING_NEXT")
            return {
                "state": "error" if self.state == "ERROR" else ("standby" if self.state == "STANDBY" else
                                                                ("playing" if cur else "starting")),
                "phase": self.state,
                "renderer": "mpv",
                "renderer_note": self.note,
                "current": ({"media_id": (self.transition or cur).get("media_id") if self.state == "PLAYING_TRANSITION"
                             else cur.get("media_id"),
                             "name": ("Overgang: " + self.transition.get("name", "")) if self.state == "PLAYING_TRANSITION"
                             else cur.get("name"),
                             "kind": cur.get("kind"), "index": self.index, "total": len(self.items),
                             "item_id": cur.get("item_id"),
                             "shuffle": self._shuffle(self.cfg, self.fallback_active),
                             "transition": self.state == "PLAYING_TRANSITION"}
                            if cur and (showing or self.state == "PLAYING_TRANSITION") else None),
                "playlist_id": playlist.get("id"),
                "revision": (self.cfg or {}).get("revision"),
                "mpv_restarts": self.total_restarts,
                "errors": list(self.errors),
                "display": display_info(),
                "hwdec": hw,
                "version": __version__,
                "uptime": time.time() - self.started,
                "message": self.message or self.syncer.last_error,
                "control": {"v": 1, "last": self.last_control},
                "video_stats": dict(self.video_stats),
            }

    # -- configuratie --------------------------------------------------------------
    def load_cache(self):
        cfg = load_cached_config(self.cache_dir)
        if cfg:
            log.info("Laatst bekende playlist uit cache geladen (revisie %s).", cfg.get("revision"))
            self.syncer.revision = cfg.get("revision")
        return cfg

    def _playlist_id(self, cfg):
        return ((cfg or {}).get("playlist") or {}).get("id")

    def on_config(self, cfg):
        """Nieuwe configuratie. Andere playlist => direct wisselen; dezelfde playlist
        (bewerkt) => bij de volgende itemgrens, zodat lopende items en overgangen netjes
        afmaken en er geen race ontstaat."""
        self.syncer.revision = cfg.get("revision")
        immediate = (self.cfg is None or self.state in ("STARTING", "STANDBY", "ERROR", "HOLD")
                     or self._playlist_id(cfg) != self._playlist_id(self.cfg) or not self.items)
        if immediate:
            log.info("Configuratie actief: playlist %s, %d item(s).",
                     (cfg.get("playlist") or {}).get("name") or "-", len(cfg.get("items") or []))
            self.cfg = cfg
            self.pending = None
            self.items = self._round(cfg.get("items") or [], self._shuffle(cfg, False), cfg)
            self.fallback_active = False
            self.index = -1
            self.consecutive_failures = 0
            self.forced = None
            self.played = []
            self.apply_properties()
            if self.mpv.alive():
                self.go_next()
            else:
                self.current = None
        else:
            log.info("Bijgewerkte playlist wordt actief na het huidige item.")
            self.pending = cfg
            self.apply_properties(cfg)

    def apply_properties(self, cfg=None):
        cfg = cfg or self.cfg
        if not cfg or not self.mpv.alive():
            return
        wanted = {"volume": int(cfg.get("volume", 80)),
                  "video-rotate": int((cfg.get("player") or {}).get("rotation", 0)),
                  "mute": bool((cfg.get("playlist") or {}).get("mute", False))}
        self._set_props(wanted)

    def _set_props(self, wanted):
        for k, v in wanted.items():
            if self.applied.get(k) != v:
                try:
                    self.mpv.set(k, v)
                    self.applied[k] = v
                except MpvError as exc:
                    self.error(f"Instelling {k} toepassen mislukt: {exc}", once_key=f"prop-{k}")

    def apply_scale(self, mode):
        self._set_props(SCALE_PROPS.get(mode, SCALE_PROPS["cover"]))

    # -- mpv-beheer ----------------------------------------------------------------
    def ensure_mpv(self):
        if self.mpv.alive():
            return True
        now = time.time()
        if now < self.cooldown_until or now < self.next_start_at:
            return False
        self.restarts = [t for t in self.restarts if now - t < self.RESTART_WINDOW]
        if len(self.restarts) >= self.MAX_RESTARTS:
            self.cooldown_until = now + self.COOLDOWN
            self.restarts = []
            self.set_state("ERROR")
            self.message = "mpv crasht herhaaldelijk; nieuwe poging over 60 s."
            self.error(self.message)
            return False
        is_restart = self.mpv_started_once
        if is_restart:
            log.warning("mpv opnieuw starten.")
            self.total_restarts += 1
        self.restarts.append(now)
        try:
            self.mpv.initial_rotation = int(((self.cfg or {}).get("player") or {}).get("rotation", 0))
            self.mpv.initial_volume = int((self.cfg or {}).get("volume", 80))
            self.mpv.start()
        except MpvError as exc:
            self.set_state("ERROR")
            self.message = str(exc)
            self.error(str(exc))
            self.next_start_at = time.time() + 5
            return False
        self.mpv_started_once = True
        self.message = None
        self.applied = {}
        self.video_loaded = False
        self.overlay = None
        self.screen_size = None
        self.apply_properties()
        if self.state == "STANDBY":
            self.show_standby(self.standby_reason or "")
        elif self.cfg is not None:
            # Na een (her)start: volgende item, zodat één kapot bestand geen herstartlus geeft.
            self.go_next()
        return self.mpv.alive()

    def restart_mpv(self, reason):
        self.error(f"mpv wordt herstart: {reason}")
        self.mpv.stop()

    # -- logo-overlay ---------------------------------------------------------------
    LOGO_MAX = 0.18      # maximaal 18% van breedte en hoogte (zelfde als browserweergave)
    LOGO_MARGIN = 0.03   # 3% van de kortste schermzijde

    def _screen(self):
        if not self.screen_size:
            try:
                w, h = int(self.mpv.get("osd-width", 0) or 0), int(self.mpv.get("osd-height", 0) or 0)
            except (MpvError, TypeError, ValueError):
                w = h = 0
            if w > 0 and h > 0:
                self.screen_size = (w, h)
        return self.screen_size or (1920, 1080)

    def _logo_overlay(self, path):
        """Zet een PNG om naar een BGRA-bestand (voorvermenigvuldigde alfa) voor overlay-add."""
        from PIL import Image, ImageChops
        sw, sh = self._screen()
        rot = int(((self.cfg or {}).get("player") or {}).get("rotation", 0)) % 360
        cw, ch = (sh, sw) if rot in (90, 270) else (sw, sh)
        st = os.stat(path)
        key = hashlib.sha256(f"{path}|{st.st_size}|{st.st_mtime}|{sw}x{sh}|{rot}".encode()).hexdigest()[:24]
        raw = os.path.join(self.cache_dir, f"logo-{key}.bgra")
        meta = raw + ".txt"
        if not (os.path.exists(raw) and os.path.exists(meta)):
            with Image.open(path) as im:
                im = im.convert("RGBA")
                im.thumbnail((max(1, int(cw * self.LOGO_MAX)), max(1, int(ch * self.LOGO_MAX))))
                if rot:
                    im = im.rotate(-rot, expand=True)   # mee draaien met de schermstand
                r, g, b, a = im.split()
                r, g, b = (ImageChops.multiply(c, a) for c in (r, g, b))
                data = Image.merge("RGBA", (b, g, r, a)).tobytes()
                w, h = im.size
            with open(raw + ".tmp", "wb") as fh:
                fh.write(data)
            os.replace(raw + ".tmp", raw)
            with open(meta, "w") as fh:
                fh.write(f"{w} {h}")
            olds = sorted((f for f in os.listdir(self.cache_dir) if f.startswith("logo-") and f.endswith(".bgra")),
                          key=lambda f: os.path.getmtime(os.path.join(self.cache_dir, f)))
            for f in olds[:-20]:
                for x in (f, f + ".txt"):
                    try:
                        os.unlink(os.path.join(self.cache_dir, x))
                    except OSError:
                        pass
        with open(meta) as fh:
            w, h = (int(v) for v in fh.read().split())
        m = round(self.LOGO_MARGIN * min(sw, sh))
        # Linksboven van de inhoud; bij een gedraaid scherm de bijbehorende hoek.
        x, y = {0: (m, m), 90: (sw - w - m, m), 180: (sw - w - m, sh - h - m), 270: (m, sh - h - m)}.get(rot, (m, m))
        return (raw, max(0, x), max(0, y), w, h)

    def set_logo(self, logo):
        want = None
        if logo and logo.get("local") and os.path.isfile(logo["local"]):
            try:
                want = self._logo_overlay(logo["local"])
            except Exception as exc:  # noqa: BLE001
                self.error(f"Logo kan niet worden getoond: {exc}", once_key=("logo", logo.get("media_id")))
        if want == self.overlay:
            return
        try:
            if want is None:
                self.mpv.command("overlay-remove", 0)
            else:
                raw, x, y, w, h = want
                self.mpv.command("overlay-add", 0, x, y, raw, 0, "bgra", w, h, w * 4)
            self.overlay = want
        except MpvError as exc:
            self.error(f"Logo-overlay mislukt: {exc}", once_key="overlay")
            self.overlay = None

    # -- afspelen ------------------------------------------------------------------
    def _collect_video_stats(self):
        """Overgeslagen en getoonde frames van de video die nu stopt bij het totaal optellen."""
        if not self.video_loaded:
            return
        self.video_loaded = False
        try:
            dropped = int(self.mpv.get("frame-drop-count", 0) or 0) + int(self.mpv.get("decoder-frame-drop-count", 0) or 0)
            frames = int(self.mpv.get("estimated-frame-number", 0) or 0)
        except (MpvError, TypeError, ValueError):
            return
        with self.lock:
            self.video_stats["dropped"] += dropped
            self.video_stats["total"] += max(frames, dropped)

    def _load(self, path, scale, logo=None):
        self._collect_video_stats()
        self.apply_scale(scale)
        self.mpv.command("loadfile", path, "replace")
        # Met --keep-open pauzeert mpv aan het einde van een video, en die pauze
        # blijft gelden voor het volgende bestand. Daarom altijd expliciet hervatten.
        self.mpv.set("pause", False)
        self.set_logo(logo)
        self.loaded_at = time.time()

    def show_standby(self, reason):
        self.set_state("STANDBY")
        self.standby_reason = reason
        self.current = None
        self.transition = None
        self.deadline = None
        self.standby_until = time.time() + self.STANDBY_RETRY
        sb = ((self.cfg or {}).get("standby") or {}).get("local")
        if sb and os.path.isfile(sb) and self.mpv.alive():
            try:
                self._load(sb, "contain")
            except MpvError as exc:
                self.error(f"Standby-scherm tonen mislukt: {exc}")
        log.info("Standby-scherm: %s", reason)

    def load_item(self, item):
        self.current = item
        self.transition = None
        self.set_state("LOADING_NEXT", item.get("name"))
        if item.get("kind") == "html":
            self.error("HTML-pagina's worden alleen in de browserweergave getoond; "
                       f"'{item.get('name')}' wordt overgeslagen.", once_key=("html", item.get("media_id")))
            self.fail(None)
            return
        path = item.get("local")
        if not path or not os.path.isfile(path):
            self.fail(f"Bestand ontbreekt: {item.get('name')}")
            return
        try:
            self._load(path, item.get("scale") or (self.cfg or {}).get("scale_default", "cover"), item.get("logo"))
        except MpvError as exc:
            self.restart_mpv(str(exc))
            return
        self.video_loaded = item.get("kind") == "video"
        if not (self.played and self.played[-1][0] is self.items and self.played[-1][1] == self.index):
            self.played.append((self.items, self.index, self.fallback_active))
            self.played = self.played[-50:]
        self.set_state("PLAYING", item.get("name"))
        if not self.fallback_active and self._shuffle(self.cfg, False):
            self._mark_played(item)
        if item.get("kind") == "image":
            self.deadline = self.loaded_at + float(item.get("duration") or 10)
        else:
            self.deadline = None

    def fail(self, text):
        if text:
            self.error(text)
        self.consecutive_failures += 1
        if self.consecutive_failures >= max(1, len(self.items)):
            self.show_standby("geen afspeelbare bestanden")
        else:
            self.go_next()

    def _next_position(self):
        """Volgende (index, lijst-wissel) inclusief het toepassen van een bijgewerkte playlist."""
        if self.forced is not None:          # gekozen via de live bediening
            items, idx, fb = self.forced
            self.forced = None
            self.items = items
            self.fallback_active = fb
            return idx
        if self.pending is not None:
            new = self.pending
            self.pending = None
            cur_id = (self.current or {}).get("media_id")
            nxt = 0
            if self._shuffle(new, False):
                new_items = self._round(new.get("items") or [], True, new)   # bewerkt: ronde loopt door
            else:
                new_items = list(new.get("items") or [])
                if 0 <= self.index < len(new_items) and new_items[self.index].get("media_id") == cur_id:
                    nxt = self.index + 1
            self.cfg = new
            self.items = new_items
            self.fallback_active = False
            self.consecutive_failures = 0
            self.played = []
            log.info("Bijgewerkte playlist actief (%d item(s)).", len(new_items))
            if nxt >= len(new_items):
                return self._wrap()
            return nxt
        nxt = self.index + 1
        if nxt >= len(self.items):
            return self._wrap()
        return nxt

    def _wrap(self):
        mode = ((self.cfg or {}).get("playlist") or {}).get("repeat_mode", "loop")
        if mode == "once_hold_last" and not self.fallback_active:
            return "hold"
        if mode == "once_then_default" and not self.fallback_active and (self.cfg or {}).get("fallback_items"):
            log.info("Playlist eenmaal afgespeeld; overschakelen naar standaardplaylist.")
            self.items = self._round(self.cfg["fallback_items"], self._shuffle(self.cfg, True))
            self.fallback_active = True
            self.consecutive_failures = 0
            return 0
        if self._shuffle(self.cfg, self.fallback_active):
            base = (self.cfg or {}).get("fallback_items" if self.fallback_active else "items") or self.items
            self.items = self._round(base, True, None if self.fallback_active else self.cfg)
        return 0

    @staticmethod
    def _shuffle(cfg, fallback):
        pl = (cfg or {}).get("playlist") or {}
        return bool(pl.get("fallback_shuffle") if fallback else pl.get("shuffle"))

    @staticmethod
    def _item_key(it):
        return f"i{it['item_id']}" if it.get("item_id") is not None else f"m{it.get('media_id')}"

    def _load_round(self):
        try:
            with open(self.round_file, encoding="utf-8") as fh:
                r = json.load(fh)
            if isinstance(r, dict) and isinstance(r.get("done"), list):
                return {"pl": r.get("pl"), "done": [str(k) for k in r["done"]][:5000]}
        except (OSError, ValueError):
            pass
        return {"pl": None, "done": []}

    def _mark_played(self, item):
        """Item speelde in de lopende willekeurige ronde. Speelt een item dat al geweest was,
        dan is er een nieuwe ronde begonnen."""
        pl, key = self._playlist_id(self.cfg), self._item_key(item)
        if self.round["pl"] != pl or key in self.round["done"]:
            self.round = {"pl": pl, "done": []}
        self.round["done"].append(key)
        try:
            tmp = self.round_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.round, fh)
            os.replace(tmp, self.round_file)
        except OSError:
            pass

    def _round(self, base, shuffle, cfg=None):
        """Lijst voor een (deel)ronde. Met 'cfg' (hoofdplaylist): eerst de items die in de lopende
        ronde nog niet speelden; zijn die er niet meer, dan een volledige nieuwe ronde."""
        items = list(base)
        if shuffle and cfg is not None and self.round["pl"] == self._playlist_id(cfg):
            rest = [it for it in items if self._item_key(it) not in self.round["done"]]
            if rest:
                items = rest
        if shuffle and len(items) > 1:
            random.shuffle(items)
            last = (self.current or {}).get("media_id")
            if last is not None and items[0].get("media_id") == last:
                k = random.randrange(1, len(items))
                items[0], items[k] = items[k], items[0]
        return items

    def go_next(self):
        if self.pending is None and not self.items:
            self.show_standby("lege of geen playlist")
            return
        pos = self._next_position()
        if not self.items:
            self.show_standby("lege of geen playlist")
            return
        if pos == "hold":
            if self.state != "HOLD":
                log.info("Playlist eenmaal afgespeeld; laatste item blijft staan.")
            self.set_state("HOLD", (self.current or {}).get("name"))
            self.deadline = None
            return
        self.index = pos
        self.load_item(self.items[self.index])

    def item_done(self):
        """Huidig item is volledig getoond: eventueel overgang, dan volgende item."""
        self.consecutive_failures = 0
        mode = ((self.cfg or {}).get("playlist") or {}).get("repeat_mode", "loop")
        is_last = self.index >= len(self.items) - 1
        if (is_last and mode == "once_hold_last" and not self.fallback_active and self.pending is None
                and self.forced is None):
            self.go_next()      # => HOLD, zonder overgang
            return
        tid = (self.current or {}).get("transition")
        t = ((self.cfg or {}).get("transitions") or {}).get(tid) if tid else None
        if t is None:
            self.go_next()
            return
        self.set_state("PREPARE_TRANSITION", t.get("name"))
        path = t.get("local")
        if not path or not os.path.isfile(path) or os.path.getsize(path) == 0:
            self.error(f"Overgang '{t.get('name')}' ontbreekt; overgeslagen.", once_key=("tmiss", tid))
            self.go_next()
            return
        try:
            self._load(path, (self.cfg or {}).get("scale_default", "cover"))
        except MpvError as exc:
            self.restart_mpv(str(exc))
            return
        self.video_loaded = True
        self.transition = t
        self.deadline = None
        self.set_state("PLAYING_TRANSITION", t.get("name"))

    def transition_done(self, failed=None):
        if failed:
            self.error(f"Overgang '{(self.transition or {}).get('name')}' overgeslagen: {failed}")
        self.transition = None
        self.go_next()

    # -- live bediening ---------------------------------------------------------------
    def _adopt_pending(self):
        """Een bijgewerkte versie van de playlist direct actief maken (de website toont al
        de nieuwe versie, dus 'naar item' moet daarin zoeken)."""
        if self.pending is None:
            return
        new, self.pending = self.pending, None
        cur = self.current or {}
        base = new.get("items") or []
        self.cfg = new
        if self._shuffle(new, False):
            # De ronde loopt door: het huidige item vooraan, daarna wat in deze ronde nog niet speelde.
            same = [it for it in base if it.get("item_id") is not None and it.get("item_id") == cur.get("item_id")]
            rest = [it for it in self._round(base, True, new) if not same or it is not same[0]]
            self.items = same[:1] + rest
        else:
            self.items = list(base)
        self.index = next((i for i, it in enumerate(self.items)
                           if it.get("item_id") is not None and it.get("item_id") == cur.get("item_id")), -1)
        if self.index > 0 and self._shuffle(new, False):
            # Nieuwe willekeurige ronde die met het huidige item begint: alle andere items
            # volgen daarna nog in deze ronde (anders zou een deel van de ronde wegvallen).
            self.items[0], self.items[self.index] = self.items[self.index], self.items[0]
            self.index = 0
        self.fallback_active = False
        self.played = []

    def _manual_target(self, cmd):
        """(lijst, index, fallback) voor een opdracht, of een tekst als het niet kan."""
        cfg = self.cfg or {}
        base = cfg.get("items") or []
        if not base:
            return "er staat niets in de playlist"
        fb = self.fallback_active
        action = cmd.get("action")
        if action == "next":
            if 0 <= self.index + 1 < len(self.items):
                return (self.items, self.index + 1, fb)
            mode = (cfg.get("playlist") or {}).get("repeat_mode", "loop")
            if mode == "once_then_default" and not fb and cfg.get("fallback_items"):
                return (self._round(cfg["fallback_items"], self._shuffle(cfg, True)), 0, True)
            src = (cfg.get("fallback_items") if fb else base) or base
            return (self._round(src, self._shuffle(cfg, fb)), 0, fb)   # ook bij 'laatste blijft staan'
        if action == "prev":
            if not self.items:
                return "er is geen vorig item"
            if self.state == "PLAYING_TRANSITION" and 0 <= self.index < len(self.items):
                return (self.items, self.index, fb)        # het item van vóór de overgang
            if self._shuffle(cfg, fb):
                if len(self.played) >= 2:
                    self.played.pop()                         # huidig item
                    return self.played[-1]
                if self.index > 0:
                    return (self.items, self.index - 1, fb)
                return "er is nog geen vorig item in deze willekeurige ronde"
            return (self.items, (self.index - 1) % len(self.items) if self.index >= 0 else len(self.items) - 1, fb)
        if action == "goto":
            want = cmd.get("item_id")
            pos = next((i for i, it in enumerate(base) if it.get("item_id") == want), None)
            if pos is None:
                return "dit item staat niet in de playlist op het scherm (bestand ontbreekt of verouderd)"
            if not fb:
                for i, it in enumerate(self.items):
                    if it.get("item_id") == want:
                        return (self.items, i, False)
            if self._shuffle(cfg, False):
                lst = self._round(base, True)
                j = next(i for i, it in enumerate(lst) if it.get("item_id") == want)
                lst[0], lst[j] = lst[j], lst[0]
                return (lst, 0, False)
            return (list(base), pos, False)
        return "onbekende opdracht"

    def control(self, cmd):
        if self.cfg is None or self.state in ("STARTING", "ERROR") or not self.mpv.alive():
            result = "de player is nog niet klaar"
        else:
            self._adopt_pending()
            target = self._manual_target(cmd)
            if isinstance(target, str):
                result = target
            else:
                tid = (self.current or {}).get("transition")
                with_t = (bool(cmd.get("transition")) and self.state == "PLAYING"
                          and tid in ((self.cfg or {}).get("transitions") or {}))
                self.forced = target
                self.consecutive_failures = 0
                if with_t:
                    self.item_done()          # eerst de overgang, dan het gekozen item
                else:
                    self.transition = None
                    self.go_next()
                result = "ok"
        log.info("Live bediening: %s -> %s", cmd.get("action"), result)
        self.last_control = {"seq": cmd.get("seq"), "action": cmd.get("action"), "result": result}
        self.syncer.kick()

    def tick(self):
        now = time.time()
        try:
            cfg, errors = self.queue.get_nowait()
            for e in errors:
                self.error(e)
            self.on_config(cfg)
        except queue.Empty:
            pass

        if not self.ensure_mpv():
            return
        try:
            cmd = self.commands.get_nowait()
        except queue.Empty:
            cmd = None
        if cmd is not None:
            self.control(cmd)
            return
        if self.cfg is not None and self.current is None and self.state not in ("STANDBY", "HOLD", "PLAYING_TRANSITION"):
            self.go_next()
            return

        try:
            events = self.mpv.drain_events(0)
        except MpvError as exc:
            self.restart_mpv(str(exc))
            return
        for ev in events:
            if ev.get("event") == "end-file" and ev.get("reason") == "error":
                detail = ev.get("file_error", "onbekende fout")
                # Bij 'loadfile replace' eindigt het vorige bestand met reason=stop; een
                # error hoort dus bij het zojuist geladen bestand.
                if self.state == "PLAYING_TRANSITION":
                    self.transition_done(failed=detail)
                    return
                if self.state in ("PLAYING", "LOADING_NEXT") and self.current is not None:
                    self.fail(f"Kan '{self.current.get('name')}' niet afspelen ({detail}).")
                    return
                log.warning("mpv kon bestand niet afspelen: %s", detail)

        if self.state == "STANDBY":
            if now >= self.standby_until and (self.items or self.pending):
                self.consecutive_failures = 0
                self.index = -1
                self.set_state("LOADING_NEXT")
                self.go_next()
            return
        if self.state not in ("PLAYING", "PLAYING_TRANSITION"):
            return

        is_video = self.state == "PLAYING_TRANSITION" or (self.current or {}).get("kind") == "video"
        if now - self.last_ping >= (0.5 if is_video else self.PING_INTERVAL):
            self.last_ping = now
            try:
                idle = self.mpv.get("idle-active", False)
                eof = self.mpv.get("eof-reached", False) if is_video else False
                if self.applied.get("hwdec-current") is None and is_video and now - self.loaded_at > 0.5:
                    hw = self.mpv.get("hwdec-current", None)
                    if hw is not None:
                        self.applied["hwdec-current"] = hw
            except MpvError as exc:
                self.restart_mpv(f"reageert niet ({exc})")
                return
            since = now - self.loaded_at
            if self.state == "PLAYING_TRANSITION":
                vd = (self.transition or {}).get("video_duration") or 30
                if idle and since > self.FAIL_IDLE_AFTER:
                    self.transition_done(failed="kan niet worden afgespeeld")
                elif eof and since > 0.5:
                    self.transition_done()
                elif since > vd * 1.5 + 10:
                    self.restart_mpv("overgang loopt vast")
                    self.transition_done(failed="liep vast")
                return
            if idle and since > self.FAIL_IDLE_AFTER:
                self.fail(f"Kan '{self.current.get('name')}' niet afspelen (beschadigd of niet ondersteund).")
                return
            if eof and since > 1.0:
                self.item_done()
                return
            vd = self.current.get("video_duration")
            if self.current.get("kind") == "video" and vd and since > vd * 1.5 + 30:
                self.restart_mpv(f"video '{self.current.get('name')}' loopt vast")
                return
            if not idle and since > self.FAIL_IDLE_AFTER:
                self.consecutive_failures = 0

        if self.state == "PLAYING" and self.deadline is not None and now >= self.deadline:
            self.item_done()

    def run(self):
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, "running", False))
        cached = self.load_cache()
        if cached:
            self.queue.put((cached, []))
        self.syncer.start()
        sd_notify("READY=1")
        log.info("Signage-player %s gestart (mpv-weergave).", __version__)
        while self.running:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001
                log.exception("Onverwachte fout in de player: %s", exc)
                self.error(f"Interne fout: {exc}")
                time.sleep(1)
            sd_notify("WATCHDOG=1")
            time.sleep(0.2)
        sd_notify("STOPPING=1")
        self.syncer.stop()
        self.mpv.stop()

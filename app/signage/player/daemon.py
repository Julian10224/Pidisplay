"""Startpunt van de signage-player (signage-player.service).

Kiest de weergave:
  browser  Chromium-kiosk (onder cage): foto's, video's, HTML en overgangen met voorgeladen lagen.
  mpv      mpv direct op het scherm: foto's, video's en overgangen (geen HTML).
  auto     (standaard) browser als Chromium en een grafische omgeving beschikbaar zijn, anders mpv.

De keuze komt uit player.ini ('renderer') of de optie --renderer (gebruikt door het
startscript signage-player-launch, dat ook automatisch terugvalt op mpv als de browser-
weergave herhaaldelijk niet start).
"""

import argparse
import logging
import os
import sys

from ..config import read_player_config
from .browser import BrowserRenderer, find_chromium
from .mpv_renderer import MpvRenderer
from .sync import Syncer, display_info, sd_notify  # noqa: F401  (hergebruikt door tests)

log = logging.getLogger("signage.player")

DEFAULT_PLAYER_CONFIG = "/etc/signage/player.ini"

# Compatibiliteit: in 1.0 heette de (mpv-)player 'Player'.
Player = MpvRenderer


def choose_renderer(pcfg, override=None):
    wanted = (override or pcfg.get("renderer", "auto") or "auto").strip().lower()
    if wanted == "mpv":
        return "mpv"
    has_gui = bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))
    has_chromium = find_chromium(pcfg.get("chromium_binary", "auto")) is not None
    if wanted == "browser" or (wanted == "auto" and has_gui and has_chromium):
        return "browser"
    return "mpv"


def main(argv=None):
    logging.basicConfig(level=os.environ.get("SIGNAGE_LOG_LEVEL", "INFO"),
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    ap = argparse.ArgumentParser()
    ap.add_argument("--renderer", choices=("auto", "browser", "mpv"))
    ap.add_argument("--note", help="Melding voor het dashboard (bijv. reden van terugval)")
    args = ap.parse_args(argv)
    path = os.environ.get("SIGNAGE_PLAYER_CONFIG", DEFAULT_PLAYER_CONFIG)
    pcfg = read_player_config(path)
    if not pcfg.get("token"):
        log.error("Geen player-token in %s; de player kan zich niet aanmelden.", path)
    renderer = choose_renderer(pcfg, args.renderer)
    log.info("Weergave: %s", renderer)
    if renderer == "browser":
        BrowserRenderer(pcfg).run()
    else:
        MpvRenderer(pcfg, note=args.note).run()


if __name__ == "__main__":
    main()

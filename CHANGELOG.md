# Wijzigingen

## 1.2.0 — willekeurige volgorde, logo per item en media-overzicht

Nieuw:

- **Willekeurige volgorde** per playlist (schakelaar in de playlist-editor). Elke ronde
  opnieuw geschud, elk item één keer per ronde, nooit hetzelfde item twee keer achter elkaar.
  Werkt in de browser- en de mpv-weergave, ook met overgangen, logo's en herhaalinstellingen.
- **Logo per item**: een PNG linksboven (max. 18% van breedte/hoogte, marge 3%), per item te
  kiezen of in één keer voor alle items. Het logo hoort bij het item (niet bij de overgang),
  draait mee met de schermstand en werkt in beide weergaven (in mpv als overlay).
- **Media als volledig overzicht ('assets')**: opslaggebruik (media, vrije ruimte, back-ups),
  ruimte per bestand, waar elk bestand gebruikt wordt (playlist, overgang, logo, niet gebruikt),
  filters op PNG en 'niet gebruikt', sorteren op ruimte, en meerdere bestanden tegelijk
  verwijderen met één bevestiging (en een tweede bij bestanden die in gebruik zijn).
- Voorbeeld in de editor toont logo's en willekeurige volgorde; het dashboard markeert het
  huidige item ook bij willekeurige volgorde goed.

Databasemigratie 2 → 3 (automatisch, met back-up vooraf): `playlists.shuffle` (standaard uit)
en `playlist_items.logo_media_id` (standaard leeg). Bestaande playlists veranderen niet.

API: `PUT /api/playlists/<id>` accepteert `shuffle` en per item `logo_media_id` (alleen PNG);
`GET /api/media` geeft `summary`, `storage_bytes` en `transparent`; nieuw
`POST /api/media/delete` voor meerdere bestanden. De installer installeert `python3-pil` nu
ook voor een losse player (nodig voor het logo in de mpv-weergave).

Tests: 87 automatische tests geslaagd (nieuw: `test_v12.py`, shuffle en logo in beide
weergaven), logo met de echte mpv op een virtueel scherm (`tests/mpv_logo_check.py`, ook bij
90° gedraaid), beheerwebsite 11 nieuwe stappen, upgrade 1.1.1 → 1.2.0 van een geïnstalleerd
systeem met gegevens en `tests/check_installed_v12.py` 10/10.

## 1.1.1 — controle van de volledige code tegen beide opdrachten

Gevonden en opgelost:

- **Geheugenlek bij overgangen** (browserweergave): elke overgang liet een event-listener
  achter op de overgangsvideo; bij langdurig draaien liep dat op. Nu worden alle luisteraars
  en timers per overgang opgeruimd.
- **Isolatie van HTML-content**: een geüploade pagina kon via de lokale playerserver de
  status van de player lezen en een valse status sturen. De server levert de player nu
  alleen op 127.0.0.1 en HTML-content alleen op localhost.
- **Heel korte video's** konden afgelopen zijn voordat de player luisterde; de player liep
  dan pas na de time-out door. Luisteraars worden nu vóór het starten gekoppeld.
- **HTML werd te vroeg vooraf geladen**: animaties in de pagina liepen al terwijl die nog
  onzichtbaar was. HTML wordt nu pas kort (5 s) voor het einde van het vorige item geladen.
- **Volume/geluid van overgangen** volgde een gewijzigde instelling pas na een playlistwissel.
- **Terugdraaien naar 1.0** (`signage-admin rollback`) zou de player-service breken (1.0 heeft
  geen startscript); de service start zo'n versie nu direct, en rollback wijst op de
  back-up van vóór de databasemigratie.
- **Installer** meldde in één run zowel 'browserweergave beschikbaar' als 'niet beschikbaar'.

Toegevoegd:

- **Weergave/kioskmodus instelbaar in de beheerwebsite** (*Instellingen → Weergave*):
  automatisch, browser of mpv, en 'browserweergave opnieuw proberen' na een automatische
  terugval (eis 'player- en kioskmodus' in de instellingen).

Tests: 72 automatische tests geslaagd (nieuw: korte video, HTML niet te vroeg geladen,
isolatie van de lokale playerserver, weergave-instelling), beheerwebsite 22 + 14 stappen,
installatie/update/rollback en `check_installed_v11.py` 13/13 op het geïnstalleerde testsysteem.

## 1.1.0 — overgangsvideo's, HTML-content en beeldvullende weergave

### Inventarisatie van de bestaande code (1.0.0)

| Onderdeel | Waar | Hoe het in 1.0 werkte |
|---|---|---|
| Playlistbeheer | `routes_playlists.py`, editor in `static/app.js` | Items (media, positie, duur) in `playlist_items`; volgorde vervangen bij opslaan. |
| Afspelen | `player/daemon.py` + `player/mpv.py` | Eén mpv-proces via JSON-IPC; daemon regelde timing, overslaan en herstel. |
| Databasevelden | `db.py` (schema 1) | `media.kind` ∈ {image, video}; `playlist_items.duration`; geen schaal- of overgangsvelden. |
| Media-opslag | `/var/lib/signage/media/{files,renditions,thumbs}` | Origineel + afspeelversie (foto's verkleind tot 1920×1080 *passend*). |
| Schaaltechniek | mpv-standaard | Altijd *passend*: zwarte randen bij afwijkende verhoudingen. |
| Volgende item | `Player.next_item()` | Timer (foto) of `eof-reached` (video), dan direct `loadfile` van het volgende item. |

Alles is in deze bestaande componenten uitgebreid; er is geen tweede systeem gebouwd.
Versie 1.0.0 is vóór de wijzigingen volledig bewaard (back-up en `signage-1.0.0.zip`).

### Nieuw

- **Overgangsvideo's**: per playlist aan/uit, standaardovergang uit de mediabibliotheek,
  per item *standaard / geen / eigen video*, standaard voor nieuwe items, voorbeeld bekijken.
  Volgorde: item → volledige overgang → volgend item, ook van het laatste naar het eerste item.
- **HTML-content**: upload van `.html` of `.zip` (veilig uitgepakt, één map per project, vast
  startbestand), ingebouwde lokale pagina's via een whitelist (Klok, Welkom), instelbare duur,
  optioneel ontwerpformaat. Voorvertoning in een sandbox.
- **Beeldvullend**: schaalmodi *Vullen* (standaard), *Passend* en *Uitrekken*, globaal en per
  item. Afspeelversies van foto's zijn nu groot genoeg om het scherm te bedekken (max. 2048 px).
- **Browserweergave**: Chromium-kiosk onder cage met een lokale playerpagina die elk item vooraf
  in een eigen laag laadt (geen zwart beeld tussen items), HTML-isolatie per proces, toezicht op
  proces, levenstekens en geheugen. **mpv-weergave** blijft als automatische terugval.
- **Toestandsmachine** in beide weergaven: LOADING_NEXT, STARTING_NEXT, PLAYING,
  PREPARE_TRANSITION, PLAYING_TRANSITION, HOLD, STANDBY, ERROR, met generatieteller tegen
  raceconditions.
- Dashboard en Schermen tonen de weergave, overgeslagen videoframes / decoder en de schermgrootte.
- `signage-admin renderer [auto|browser|mpv|reset]`.

### Gewijzigde bestanden

Backend: `app/signage/app.py` (migratie met back-up, herstel HTML-projecten, nieuwe
afspeelversies), `db.py` (schema 2 + migratie), `media.py` (HTML-routing, beeldvullende
afspeelversies), `routes_media.py` (HTML, lokale pagina's, ontwerpformaat, voorvertoning),
`routes_playlists.py` (overgangen, schaalmodus), `routes_players.py` (playerconfig met
overgangen/HTML/schaal), `routes_system.py` (globale schaalmodus), `backup.py`, `config.py`.
Nieuw: `htmlpkg.py`.

Player: `player/daemon.py` (keuze weergave), `player/mpv.py` (geen verloren events, robuuster).
Nieuw: `player/sync.py`, `player/mpv_renderer.py`, `player/browser.py`,
`static/player/{player.html,player.js,player.css}`, `static/display/{clock,welcome}.html`.

Beheerwebsite: `static/app.js`, `static/styles.css`.

Installatie: `install.sh` (cage/Chromium, PAM, `--no-browser`), `uninstall.sh`,
`systemd/signage-player.service` (sessie op tty1, startscript), `bin/signage-admin`,
`config/player.ini`, `config/signage.ini`. Nieuw: `bin/signage-player-launch`,
`config/pam-signage-player`.

Tests: `tests/test_api.py`, `tests/test_player.py` aangepast; nieuw: `tests/test_v11.py`,
`tests/test_browser_player.py`, `tests/browser/`, `tests/v1_schema.sql`,
`tests/check_installed_v11.py`, `tests/mpv_visual_check.py`.

### Databasemigratie (schema 1 → 2)

Automatisch bij de eerste start van 1.1, in één transactie, met vooraf een back-up
`signage-backup-pre-migration-<datum>.tar.gz`:

- `media`: opnieuw opgebouwd (SQLite kan CHECK-regels niet wijzigen) met `kind` ∈
  {image, video, html}, `playback_dir` ∈ {files, renditions, html, builtin} en nieuwe kolommen
  `html_entry`, `html_width`, `html_height`. Alle bestaande rijen gekopieerd.
- `playlists`: `transition_enabled` (0), `transition_media_id` (NULL), `new_item_transition`
  ('default').
- `playlist_items`: `scale_mode` (NULL = globaal), `transition_mode` ('default'),
  `transition_media_id` (NULL).
- Instelling `scale_mode` = 'cover'.
- Daarna: verwijzingen gecontroleerd (`foreign_key_check`), afspeelversies van foto's eenmalig
  opnieuw gemaakt. Er zijn geen velden verwijderd.

### Tests en resultaten

- `pytest tests/`: 69 geslaagd (27 API, 14 1.1-backend, 11 mpv-player, 17 browserplayer).
- Beheerwebsite in Chromium: 22 stappen (1.0) + 15 stappen (1.1) geslaagd; mobiel zonder
  horizontaal scrollen; geen externe aanvragen.
- Echte mpv op Xvfb: schaalmodi foto en video geslaagd.
- Keten startscript → cage → daemon → Chromium: playlist speelde af; terugval naar mpv na
  drie mislukte starts werkte.
- Upgrade van een geïnstalleerd 1.0.0-systeem met gegevens naar 1.1.0 met `install.sh`:
  migratie en `check_installed_v11.py` (echte mpv) 13/13.
- Niet getest: echte Raspberry Pi, fysiek scherm, Chromium-videodecodering op de Pi 3B+,
  cage met logind op tty1, reboot. Zie README §18.

## 1.0.0

Eerste versie: lokale server met beheerwebsite, SQLite, mediabeheer, playlists, players,
mpv-player op DRM, back-up/herstel, installer, systemd-units, CLI.

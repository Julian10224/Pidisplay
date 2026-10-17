# Wijzigingen

## 1.6.0 — lichtere browserweergave

Aanleiding: de browserweergave draait op de Pi, maar met 35% overgeslagen videoframes.

- **Nooit meer dan 2 video's tegelijk geladen.** De videochip van de Pi 3B+ decodeert hooguit
  2 video's tegelijk (Chromium: "Too many decoder instances, max=2"); een derde video ging naar
  de processor. Overgangsvideo's worden nu pas geladen als het item ervoor begint en direct na
  gebruik vrijgegeven; tijdens een overgang wordt de vorige video vrijgegeven en pas dan het
  volgende item geladen. Nog steeds zonder zwart beeld (gemeten in Chromium).
- **Resolutie van de browserweergave instelbaar** (*Instellingen → Weergave*): automatisch,
  1280×720 of 1920×1080. Bij 1280×720 tekent de Pi ruim de helft minder beeldpunten; de tv
  schaalt op. Werkt via `wlr-randr` (output management van cage); de installer installeert dat.
  `browser_resolution` in `player.ini`.
- **Diagnose op het dashboard**: of Chromium met de GPU tekent (en welke), en of H.264 1080p via
  de videochip wordt gedecodeerd.
- **Emoji en Arial in HTML-pagina's**: de installer zet bij de browserweergave ook
  `fonts-noto-color-emoji` en `fonts-liberation` neer (Raspberry Pi OS Lite heeft geen emoji-letters;
  weericoontjes als ☀️ en 🌧️ bleven daardoor leeg).
- **Overgangen bij willekeurige volgorde**: nieuwe test met video's, een HTML-pagina en een foto
  in willekeurige volgorde: vóór elk item (ook het eerste van de playlist en bij elke nieuwe ronde)
  speelt de overgang. Een overgang die op de Pi werd overgeslagen kwam waarschijnlijk door de
  derde video zonder decoder (zie boven); als het nog gebeurt, staat de reden als
  "Overgang … overgeslagen" in het logboek.

## 1.5.4 — de echte oorzaak: Wayland-socket kon niet worden aangemaakt

Met de volledige uitvoer van 1.5.3 werd de oorzaak zichtbaar: cage kon zijn Wayland-socket niet
aanmaken in `/run/user/999` ("unable to open lockfile … check permissions", "Unable to open Wayland
socket") en brak daarna af. De PAM-sessie zet `XDG_RUNTIME_DIR` op `/run/user/<uid>`, maar de
service maakt `/run/user` ontoegankelijk (`ProtectHome=yes`). De browserweergave gebruikt nu de
eigen runtimemap van de service (`/run/signage-player`). In de eerdere test op de Pi (zonder
`ProtectHome`) werkte alles daarom wel.

## 1.5.3 — browserweergave start weer op de Pi (Trixie, cage 0.3)

Gevonden met tests op de Pi zelf: cage 0.3.1, de player en Chromium werken prima als cage
rechtstreeks door systemd wordt gestart (video speelde), maar braken direct af (exitcode 134)
als cage een achtergrondproces van `signage-player-launch` was.

- **cage is nu het hoofdproces van de service** (`exec`), met tty1 als invoer en de uitvoer
  rechtstreeks in het logboek — precies zoals in de geslaagde test.
- Het tellen van mislukte starts, de veiligere varianten en de foutmelding voor de website
  gebeuren nu ná het stoppen, in `signage-player-after-exit` (`ExecStopPost`). Een gewone stop
  of herstart telt niet als fout.
- **Geen 'vertalen'-balkje meer** rechtsboven in Chromium: de playerpagina is gemarkeerd als
  Nederlands en niet te vertalen (`translate="no"`, `<meta name="google" content="notranslate">`),
  Chromium start met `--lang=nl` en de vertaalfunctie staat uit in het profiel.

## 1.5.2 — logboek van de player weer zichtbaar

Aanleiding: ook met 1.5.1 bleef de foutmelding van de browserweergave leeg, en in het logboek
stonden alleen de meldingen van systemd, geen enkele regel van de player zelf.

- **Regels van de player ontbraken in het logboek**: door de sessie op tty1 (PAM) komen de processen
  van de player in een logind-sessie terecht; hun regels staan dan niet onder
  `journalctl -u signage-player`. De service heeft nu een vaste logboeknaam
  (`SyslogIdentifier=signage-player`, terug te vinden met `journalctl -t signage-player`), en de
  logboeken in de website, `signage-admin logs` en de foutmelding zoeken op unit én op die naam.
- **Fatale fouten van glibc** (abort) gingen naar de terminal (tty1, dus alleen het scherm) in plaats
  van naar het logboek; cage start nu met `LIBC_FATAL_STDERR_=1`.
- De foutmelding vermeldt ook de versie van cage.

## 1.5.1 — browserweergave: veiligere varianten en volledige foutmelding

Aanleiding: op een Pi 3B+ met Raspberry Pi OS 13 (Trixie, cage + Chromium 155) stopte de
compositor cage na 2 s met exitcode 134 (afgebroken), en de foutmelding in de website bleef leeg.

- **Foutmelding werd niet opgevangen**: na een crash van cage bleef de player-daemon nog even
  draaien, waardoor de pijp openbleef en `tail` niets wegschreef. De uitvoer wordt nu regel voor
  regel weggeschreven (eerste 3000 regels). Is er toch niets, dan komen de laatste regels uit het
  systeemlogboek in de foutmelding. Exitcodes boven 128 worden uitgelegd (134 = afgebroken).
- **Veiligere varianten**: stopt de browserweergave te snel, dan probeert de volgende start
  achtereenvolgens zonder hardwarecursor en met klassieke modesetting
  (`WLR_NO_HARDWARE_CURSORS=1 WLR_DRM_NO_ATOMIC=1`) en daarna ook zonder GPU in de compositor
  (`WLR_RENDERER=pixman`) — bekende oplossingen voor problemen van wlroots met de vc4-GPU van de Pi.
  Een variant die werkt wordt onthouden; pas als alle drie falen volgt mpv. Na een mislukte start
  logt cage uitgebreid (`-D`, als de versie dat kent). *Browserweergave opnieuw proberen* begint weer
  bij de normale variant.

## 1.5.0 — vloeiender video's, en het opstarten weer snel

Opgelost:

- **Opstarten duurde na 1.4.0 heel lang** (logo bleef staan terwijl de website al werkte): de
  nieuwe volgorde van de player-service ten opzichte van het opstartscherm liet de player wachten
  op de inlogprompt, die zelf op het einde van het opstartscherm wachtte. De player-service heeft
  weer precies de volgorde van 1.3.2; het logo blijft nu staan via een drop-in voor
  `plymouth-quit.service` (`plymouth quit --retain-splash`).
- **Haperende video's in de mpv-weergave**: mpv schaalt nu met lichte filters (bilineair, geen
  dithering, geen lineair/sigmoid schalen, geen debanding). Nieuwere mpv-versies (0.37+, Raspberry Pi
  OS Trixie) gebruiken standaard zware filters die de Pi 3B+ bij 1080p niet bijhoudt.
  Instelbaar met `render_quality` in `player.ini`.

Nieuw:

- **Video's automatisch geschikt maken voor de Pi**: HEVC, 4K, 50/60 fps, 10-bit, interlaced of een
  bitrate boven 20 Mbit/s krijgen op de achtergrond een afspeelversie (H.264 High, ≤1920×1080,
  ≤30 fps, ≤8 Mbit/s, AAC). Eerst de hardware-encoder van de Pi, anders libx264; lage voorrang;
  het resultaat wordt gecontroleerd (codec, afmetingen, duur). Origineel blijft bewaard. Voortgang en
  resultaat bij *Media*, aan/uit onder *Instellingen → Weergave*. Bestaande video's worden na de update
  ook bekeken. API: `POST /api/media/<id>/optimize`; `GET /api/media` geeft `optimize` en `optimized`.
- **Overgeslagen videoframes in de mpv-weergave** worden gemeten en op het dashboard getoond (net als
  in de browserweergave).
- Foutmelding van de browserweergave: de diagnose wacht nog maar 5 s op `chromium --version`.

Tests: 122 automatische tests (nieuw: `test_videoopt.py` met de echte ffmpeg — 60 fps, HEVC, 4K,
staande telefoonvideo met rotatie; framemeting in de mpv-weergave). Op het geïnstalleerde systeem:
omzetten met voortgang in de website. Niet op de Pi zelf gemeten.

## 1.4.0 — opstartscherm met logo en laadbalk

- **Opstartscherm**: tijdens het opstarten staat er een logo in het midden met een laadbalk
  eronder (Plymouth-thema `signage`, zwarte achtergrond). Het logo blijft staan tot de player
  het scherm overneemt (`plymouth quit --retain-splash` in de player-service, zoals een display
  manager), dus geen tekst of zwart beeld tussendoor.
- **Eigen logo** kiezen onder *Instellingen → Opstartscherm* (uit Media, bij voorkeur een PNG met
  transparantie), met voorbeeld; zonder eigen logo een neutraal standaardlogo. Het logo draait
  mee met de schermstand van de lokale player.
- `install.sh`: installeert `plymouth`, zet het thema (en werkt de initramfs bij) en voegt
  `quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0` aan
  `cmdline.txt` en `disable_splash=1` aan `config.txt` toe, met back-ups. Wat is toegevoegd staat
  in `/etc/signage/bootconfig-added`; `--no-splash` en `uninstall.sh` draaien precies dat terug.
  De optie wordt onthouden in `install.conf`.
- `signage-priv`: acties `splash` (website) en `splash-sync` (installatie). *Herstarten* wacht op
  een lopende initramfs-update.

Getest: het thema met Plymouth 24 op een virtueel scherm (0° en 90°, laadbalk loopt op), de
instelling in de beheerwebsite, installatie/upgrade met nagebootste `cmdline.txt`/`config.txt`,
terugdraaien. Niet getest op de Pi zelf. 115 automatische tests geslaagd.

## 1.3.2 — software bijwerken met een knop, foutmelding van de browserweergave

Nieuw:

- **Software bijwerken** onder *Instellingen → Software bijwerken*: controleren op een nieuwere
  versie (met wat er nieuw is), en met één knop bijwerken. De update draait als root in een eigen
  systemd-unit (`bin/signage-update`, gestart via `signage-priv`), zodat hij doorloopt terwijl de
  server herstart; de pagina volgt de voortgang en laadt zichzelf opnieuw. Ook via SSH:
  `sudo signage-admin update`. Bron vast in `/etc/signage/signage.ini` (`[update]`).
- **Foutmelding van de browserweergave**: start de browserweergave te snel niet, dan bewaart
  `signage-player-launch` de laatste uitvoer van cage, Chromium en de player plus sessie-, scherm-
  en geheugeninformatie. *Instellingen → Weergave* toont die tekst, zodat de oorzaak zonder SSH te
  zien is. De uitvoer gaat daarnaast nog steeds naar het systeemlogboek.
- **install.sh onthoudt de opties** van de eerste installatie in `/etc/signage/install.conf`, zodat
  een update (knop, `get.sh` of opnieuw `install.sh`) een losse player niet ineens tot server maakt
  en `--no-browser`, `--no-watchdog` en `--no-gpu-mem` blijven gelden.

Opgelost:

- Willekeurige volgorde: wie willekeurig aanzette terwijl de playlist speelde en daarna de live
  bediening gebruikte, kreeg een eerste ronde waarin items ontbraken. De nieuwe ronde begint nu
  met het huidige item.
- Dashboard: de teller bij willekeurige volgorde telde de plaats in de geschudde ronde
  (1, 2, 3 …), waardoor het leek alsof de playlist op volgorde speelde. Nu staat er welk item uit
  de playlist speelt (*willekeurig · item 4 van 6*); op *Schermen* staat *willekeurige volgorde*.

## 1.3.1 — installeren en updaten via SSH, zonder SD-kaart

- Nieuw `get.sh`: installeren of updaten met één opdracht via SSH, zonder iets op de
  SD-kaart te zetten:
  `curl -fsSL https://raw.githubusercontent.com/Julian10224/Pidisplay/main/get.sh | sudo bash`.
  Het script downloadt de broncode van GitHub naar een tijdelijke map, controleert dat het een
  Signage-pakket is en start `install.sh` (opties achter `-s --`, versie kiezen met `--ref`).
  Alles staat in één functie, zodat een afgebroken download nooit half wordt uitgevoerd.
- De beheerwebsite toont bij *Externe player toevoegen* nu ook deze opdracht.
- README §1, §11 en §12 bijgewerkt. Verder geen wijzigingen aan server of player.

## 1.3.0 — live bediening: het scherm vanuit de website door de playlist laten klikken

Nieuw:

- **Vorige / Volgende** op het dashboard en op *Schermen*: het echte scherm springt direct
  naar het vorige of volgende item. **Klik op een beeldje** in de filmstrip om dat item meteen
  te tonen; **← en →** werken ook. Zo loop je in een paar seconden een hele playlist door.
- **Met overgang** (aan/uit, wordt onthouden in je browser): direct wisselen, of eerst de
  overgangsvideo laten spelen om ook de overgangen te testen.
- Het dashboard ververst na een klik even twee keer per seconde en markeert het juiste item
  in de filmstrip, ook bij willekeurige volgorde (op item, niet meer op bestand).
- Players melden zich direct na elke wissel van item bij de server (niet alleen elke 15 s);
  de website laat dus sneller zien wat er op het scherm staat.

Hoe: opdrachten blijven alleen in het geheugen van de server en worden door de player met
long polling opgehaald (`GET /api/player/v1/control`), bij de browserweergave via de daemon
doorgegeven aan de playerpagina (`/cmd`). Volgnummer + epoch per serverstart: nooit dubbel
uitgevoerd, niet na een herstart; opdrachten ouder dan 15 s vervallen. Werkt in de browser- en
mpv-weergave, met overgangen, willekeurige volgorde, herhaalinstellingen en een bewerkte
playlist. De playlist zelf verandert niet.

API: nieuw `POST /api/players/<id>/control` met `{"action": "next"|"prev"|"goto", "item_id",
"transition"}` (409 als de player offline is of te oud), `GET /api/player/v1/control`; de
playerconfiguratie bevat per item `item_id`; de status van een player bevat `control` en
`current.item_id`. Geen databasemigratie; terugdraaien naar 1.2.0 kan zonder meer.

Beveiliging: de lokale playerserver (browserweergave) weigert nu een statusmelding met een
andere Origin dan de playerpagina, zodat een HTML-pagina geen valse status kan sturen.

Tests: 104 automatische tests geslaagd (nieuw: `test_v13.py` en 8 browsertests, waaronder de
hele keten tot en met de browserweergave), upgrade
1.2.0 → 1.3.0 van een geïnstalleerd systeem met de echte mpv en `tests/check_installed_v13.py`
10/10 (klik → zichtbaar in de website in 0,2–0,4 s), rollback naar 1.2.0 en terug, en de
beheerwebsite (knoppen, filmstrip, pijltjestoetsen, *Schermen*, offline, telefoonbreedte).

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

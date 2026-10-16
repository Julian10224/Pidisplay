# Signage — volledig lokaal digital-signage-systeem voor de Raspberry Pi 3B+

Eén Raspberry Pi is tegelijk **server**, **beheeromgeving** en **player**:

- een beheerwebsite (computer, tablet, telefoon) voor media, playlists, schermen en instellingen;
- een player die foto's, video's en HTML-pagina's beeldvullend op het HDMI-scherm toont,
  met optionele overgangsvideo's tussen de items;
- alles lokaal: SQLite-database, media op de SD-kaart, geen cloud, geen externe fonts of scripts.

Na de installatie is **geen internet** meer nodig. Alleen de eerste installatie (en updates
van systeempakketten) downloadt pakketten.

Versie: zie `VERSION`. Wijzigingen per versie: `CHANGELOG.md`. Controle per eis: `CONTROLE.md`.

---

## Inhoud

1. [Installatie](#1-installatie)
2. [De beheerwebsite bereiken](#2-de-beheerwebsite-bereiken)
3. [Dagelijks gebruik](#3-dagelijks-gebruik)
4. [Overgangen, schaalmodi en HTML](#4-overgangen-schaalmodi-en-html)
5. [Ondersteunde formaten](#5-ondersteunde-formaten-pi-3b)
6. [Hoe het werkt](#6-hoe-het-werkt)
7. [Offline werking](#7-offline-werking)
8. [Betrouwbaarheid](#8-betrouwbaarheid)
9. [Back-up en herstel](#9-back-up-en-herstel)
10. [Toegang herstellen](#10-toegang-herstellen)
11. [Updaten en terugdraaien](#11-updaten-en-terugdraaien)
12. [Meerdere schermen](#12-meerdere-schermen)
13. [Configuratie](#13-configuratie)
14. [Bestanden en mappen](#14-bestanden-en-mappen)
15. [Problemen oplossen](#15-problemen-oplossen)
16. [Terug naar PiSignage / verwijderen](#16-terug-naar-pisignage--verwijderen)
17. [Testverslag en acceptatiecriteria](#17-testverslag-en-acceptatiecriteria)
18. [Bekende beperkingen](#18-bekende-beperkingen)

---

## 1. Installatie

### Wat je nodig hebt

- Raspberry Pi 3B+ met goede voeding (5 V / 2,5 A).
- Een **nieuwe** microSD-kaart van minimaal 16 GB (A1/A2, merk). De kaart met PiSignage
  blijft zo ongemoeid als terugvaloptie.
- Een scherm via HDMI, en netwerk (kabel of Wi-Fi) met **eenmalig** internet.
- Een computer met [Raspberry Pi Imager](https://www.raspberrypi.com/software/).

### Stap 1 — Raspberry Pi OS Lite flashen

1. Open Raspberry Pi Imager.
2. Apparaat: **Raspberry Pi 3**. Besturingssysteem: *Raspberry Pi OS (other)* →
   **Raspberry Pi OS Lite (64-bit)**. (De 32-bit Lite-versie werkt ook.)
   Gebruik **Lite**: een desktop houdt het scherm bezet.
3. Kies bij de aanpassingen (OS customisation):
   - hostnaam, bijvoorbeeld `signage` → de website wordt dan `http://signage.local`;
   - gebruikersnaam en wachtwoord;
   - Wi-Fi (als je geen kabel gebruikt) en land/tijdzone;
   - **SSH inschakelen**.
4. Flash de kaart.

### Stap 2 — Het pakket op de SD-kaart zetten

1. Haal de kaart uit de computer en stop hem er opnieuw in. Er verschijnt een schijf
   **bootfs**.
2. Pak de zip uit en kopieer de map **`signage`** naar de hoofdmap van bootfs
   (dus `bootfs/signage/install.sh`). Download je het project als zip van GitHub, dan heet de
   map `Pidisplay-main`: hernoem die naar `signage`.
3. Werp de kaart netjes uit en stop hem in de Pi. Sluit scherm en netwerk aan en zet de Pi aan.
   De eerste start duurt een paar minuten.

### Stap 3 — Installeren (één opdracht)

Log in via SSH vanaf je computer (of met toetsenbord op de Pi zelf):

```bash
ssh <gebruikersnaam>@signage.local
sudo bash /boot/firmware/signage/install.sh
```

Het script controleert het systeem en installeert de benodigde pakketten: `mpv`, `ffmpeg`,
`python3-flask`, `python3-waitress`, `python3-pil`, `fonts-dejavu-core`, `avahi-daemon`, en
voor de browserweergave `cage` en `chromium` (op Raspberry Pi OS Bookworm `chromium-browser`).
Chromium is groot: reken op een paar minuten extra op een Pi 3B+. Wil je geen browserweergave
(dan ook geen HTML-pagina's), gebruik `--no-browser`.

Daarna maakt het een systeemgebruiker `signage`, zet de services klaar en start ze. Aan het
einde toont het de adressen van de beheerwebsite. Vraagt het om een herstart (`gpu_mem` of
watchdog), doe dan `sudo reboot`.

### Stap 4 — Beheerder aanmaken

Op het scherm verschijnt een standby-pagina met het adres van de website en een
**setup-code** (bijvoorbeeld `4821-0937`). Open het adres in je browser, vul de setup-code
in en kies een gebruikersnaam en wachtwoord. Er bestaat geen standaardwachtwoord; de code
staat alleen op het fysieke scherm (en in de uitvoer van het installatiescript).

---

## 2. De beheerwebsite bereiken

| Manier | Adres |
|---|---|
| Hostnaam (mDNS) | `http://signage.local` (of de hostnaam die je koos) |
| IP-adres | `http://192.168.x.x` — staat op het standby-scherm en in het dashboard |

- De hostnaam werkt via mDNS (avahi). Windows 10/11, macOS, iOS en de meeste Linux-systemen
  ondersteunen dat; sommige Android-toestellen niet. Het IP-adres werkt altijd.
- De hostnaam wijzig je onder *Instellingen → Netwerk*. Netwerk, Wi-Fi, SSH en firewall
  worden door dit systeem **nooit** aangepast.
- De website beantwoordt alleen verzoeken vanaf lokale/privé-adressen
  (`allow_public_clients = false`), ook als er per ongeluk een poort wordt doorgestuurd.
- Geef de Pi bij voorkeur een vast IP-adres via de DHCP-reservering van je router.

---

## 3. Dagelijks gebruik

**Dashboard** — wat er nu op het scherm staat (miniatuur van het huidige item en de volgorde
van de playlist), status van server en player, welke weergave actief is (browser of mpv) met
het percentage overgeslagen videoframes of de gebruikte decoder, vrije opslag, uptime,
temperatuur, meldingen en de belangrijkste instellingen. Je kunt hier direct een andere
playlist kiezen.

**Media** — sleep bestanden in het venster of kies ze; meerdere tegelijk, met voortgang.
Elk bestand wordt gecontroleerd (echt een afbeelding/video/HTML? codec? resolutie?). Zoeken,
filteren (foto, video, HTML), sorteren, voorvertonen, wijzigen, origineel downloaden en
verwijderen. Met *Lokale pagina toevoegen* kies je een ingebouwde schermpagina (klok,
welkom). Een bestand dat in een playlist of als overgang wordt gebruikt kan alleen na een
extra waarschuwing worden verwijderd.

**Playlists** — media toevoegen, volgorde slepen (of met pijltjestoetsen op het greepje), per
item de duur (foto's en HTML; leeg = standaardduur) en de schaalmodus, herhalen (*steeds*,
*één keer en laatste item laten staan*, *één keer en dan de standaardplaylist*), geluid van
video's aan/uit, en overgangen (zie §4). *Opslaan en activeren* zet de playlist binnen enkele
seconden op het scherm, zonder herstart. Een lege playlist of een playlist met ontbrekende
bestanden kan niet worden geactiveerd. *Voorbeeld* speelt de playlist in je browser, inclusief
overgangen en schaalmodi.

**Schermen** — de server, de players en het scherm per player: online-status, laatste
contact, wat er speelt, of de HDMI-aansluiting een scherm ziet en met welke resolutie,
schermstand (liggend/staand/ondersteboven), weergave en welke playlist de player volgt.

**Instellingen** — systeemnaam, standaardplaylist, standaardduur, volume, **schaalmodus**,
**weergave/kioskmodus** (automatisch, browser of mpv), player herstarten/uitzetten, hostnaam, tijdzone, tijdsynchronisatie, tijd handmatig instellen,
back-ups, logboeken, systeeminformatie, herstart, wachtwoord.

---

## 4. Overgangen, schaalmodi en HTML

### Overgangen

In de playlist-editor, blok *Overgangen*:

1. Zet *Overgangsvideo's gebruiken* aan.
2. Kies bij *Standaardovergang* een video uit je mediabibliotheek (*Video kiezen*) en bekijk
   hem eventueel met *Bekijken*.
3. Kies wat *nieuwe items* krijgen: de standaardovergang of geen overgang.
4. Tussen elke twee items staat een stippellijn met *Overgang naar item N*. Kies daar per
   item *Standaard*, *Geen overgang* of *Andere video…*. Onder het laatste item staat
   *Overgang terug naar item 1* (bij een herhalende playlist).

Afspeelvolgorde: item → overgangsvideo (volledig) → volgend item. Een overgang hoort bij het
item **ervoor** en verhuist mee als je dat item versleept; hij verandert nooit het mediabestand
of de volgorde. Een ontbrekende of kapotte overgangsvideo wordt overgeslagen en gemeld; de
playlist speelt door. Na het laatste item van *één keer en laten staan* speelt geen overgang.

### Schaalmodi

| Modus | Wat je ziet | Beeldverhouding |
|---|---|---|
| **Vullen** (standaard) | Hele scherm gevuld, randen zo nodig bijgesneden, nooit zwarte randen | Blijft behouden |
| **Passend** | Hele foto/video zichtbaar; er kunnen zwarte randen ontstaan | Blijft behouden |
| **Uitrekken** | Hele scherm gevuld zonder bijsnijden | Kan vervormen |

De standaard stel je in onder *Instellingen → Weergave*; per playlistitem kun je ervan
afwijken. Overgangsvideo's volgen de standaardmodus. Foto's krijgen bij het uploaden een
afspeelversie die groot genoeg is om het scherm beeldvullend te bedekken (maximaal 2048 px per
zijde, de grens van de Pi 3-GPU), zodat een staande foto op een liggend scherm niet wazig wordt.

### HTML-pagina's

- **Uploaden**: een los `.html`-bestand, of een **`.zip`** met een `index.html` en de
  bijbehorende CSS, JavaScript, afbeeldingen en fonts (een gemeenschappelijke hoofdmap in de
  ZIP mag). Gebruik **relatieve paden** (`css/stijl.css`, niet `/css/stijl.css`).
- De ZIP wordt veilig verwerkt: geen paden buiten de projectmap (`..`, absolute paden),
  geen snelkoppelingen, alleen webbestanden en media, limieten tegen ZIP-bommen. Elk project
  krijgt een eigen map (`media/html/<id>/`).
- **Lokale pagina's**: *Lokale pagina toevoegen* → *Klok* of *Welkom*. Alleen deze vaste,
  ingebouwde pagina's kunnen worden gekozen (whitelist); beheerpagina's of willekeurige
  bestanden niet.
- Een HTML-item heeft een **duur**; daarna gaat de player altijd door, ook als de pagina
  vastloopt of niet laadt. Een pagina draait in een eigen, afgeschermd proces en kan de
  player niet beïnvloeden.
- **Beeldvullend**: een pagina krijgt het volledige scherm (100vw × 100vh, zonder marges).
  Heeft een pagina een vast ontwerp (bijvoorbeeld 1920 × 1080), vul dat dan in bij *Media →
  Wijzigen → Ontwerpformaat*; de pagina wordt dan op dat formaat getekend en volgens de
  schaalmodus over het scherm geschaald.
- **Offline**: alles in het project werkt zonder internet. Laadt een pagina toch externe
  bronnen (CDN, Google Fonts, API), dan meldt het uploadscherm dat; zonder internet ontbreken
  die onderdelen, maar de rest van de playlist speelt gewoon door (de player wacht hooguit
  4 seconden op het laden).
- HTML wordt alleen in de **browserweergave** getoond (zie §6). In de mpv-terugvalweergave
  worden HTML-items overgeslagen met een melding in het dashboard.

---

## 5. Ondersteunde formaten (Pi 3B+)

| Soort | Werkt betrouwbaar | Toelichting |
|---|---|---|
| Afbeelding | PNG, JPEG, WebP | Rechtop gezet (EXIF). Max. 40 megapixel. |
| Video | **MP4 met H.264 (8-bit, yuv420p) + AAC**, tot **1920×1080 @ 30 fps** | De aanbevolen vorm, ook voor overgangsvideo's. |
| Video, met waarschuwing | H.265/HEVC, VP9, AV1, 10-bit H.264, >1080p of 60 fps bij 1080p | Geaccepteerd met waarschuwing; de Pi 3B+ decodeert dit in software en dat hapert of lukt niet. |
| HTML | `.html` of `.zip` met `index.html` | Zie §4. |
| Geweigerd | Alles wat geen echte afbeelding/video/HTML is (ook bij een misleidende extensie) | — |

Een video omzetten naar het aanbevolen formaat (op je computer, met ffmpeg):

```bash
ffmpeg -i invoer.mov -c:v libx264 -profile:v high -level 4.0 -pix_fmt yuv420p \
  -vf "scale='min(1920,iw)':-2,fps=30" -c:a aac -b:a 128k -movflags +faststart uitvoer.mp4
```

Of video's in Chromium op de Pi 3B+ hardwarematig worden gedecodeerd is niet getest (zie §18).
Controleer het percentage overgeslagen videoframes op het dashboard; is dat hoog, gebruik dan
720p-video's of kies de mpv-weergave (*Instellingen → Weergave*, of `sudo signage-admin renderer mpv`; zonder HTML).

---

## 6. Hoe het werkt

```
 Browser (pc/tablet/telefoon)            Raspberry Pi 3B+
 ─────────────────────────────    ┌──────────────────────────────────────────────────┐
                                  │ signage-server.service  (gebruiker signage)       │
   beheerwebsite  ── HTTP :80 ──▶ │  Flask + waitress · SQLite · media op SD          │
                                  │  /api/...            beheer-API (login)           │
                                  │  /api/player/v1/...  player-API (token)           │
                                  └───────────────▲──────────────────────────────────┘
                                                  │ HTTP, elke 5 s (ETag) + heartbeat
                                  ┌───────────────┴──────────────────────────────────┐
                                  │ signage-player.service  (sessie op tty1)          │
                                  │  signage-player-launch                             │
                                  │   ├ browserweergave: cage ─▶ player-daemon         │
                                  │   │    ├ lokale webserver 127.0.0.1:8765           │──▶ HDMI
                                  │   │    └ Chromium-kiosk ─▶ playerpagina (lagen)    │
                                  │   └ mpv-weergave (terugval): daemon ─▶ mpv (DRM)   │
                                  │  cache: laatst geldige playlist + media            │
                                  └──────────────────────────────────────────────────┘
```

**Twee weergaven**

- **Browserweergave** (standaard als Chromium geïnstalleerd is). Chromium draait in
  kioskmodus onder *cage*, een minimale Wayland-compositor zonder desktop. Eén lokale
  playerpagina speelt alles: elk item wordt in een eigen laag voorbereid terwijl het vorige
  nog speelt, en pas getoond als het klaar is (foto gedecodeerd, eerste videoframe geladen,
  pagina geladen). Daardoor geen zwart beeld tussen items. `object-fit: cover/contain/fill`
  verzorgt de schaalmodi. De daemon levert de pagina en de media vanaf de SD-kaart, zodat
  de player doorspeelt als de server even weg is.
- **mpv-weergave** (terugval, of `renderer = mpv`). mpv tekent direct op het scherm met de
  H.264-decoder van de Pi. Ondersteunt foto's, video's, overgangen en de schaalmodi, maar
  geen HTML. Start de browserweergave drie keer achter elkaar niet, dan schakelt het
  startscript automatisch over op mpv.

**Toestandsmachine van de player** (in beide weergaven)

```
LOADING_NEXT ─▶ STARTING_NEXT ─▶ PLAYING ─(klaar)─▶ PREPARE_TRANSITION ─▶ PLAYING_TRANSITION
     ▲                               │                                          │
     └───────────(geen overgang)─────┴─────────(overgang afgelopen of fout)──────┘
PLAYING ─(laatste item, 'eenmaal en laten staan')─▶ HOLD
niets afspeelbaar ─▶ STANDBY ─(30 s of nieuwe playlist)─▶ LOADING_NEXT;   fout ─▶ ERROR ─▶ overslaan
```

- Een overgang start alleen vanuit PLAYING, dus nooit twee keer achter elkaar of uit zichzelf.
- Een andere playlist activeren wisselt direct (het huidige beeld blijft staan tot het
  nieuwe klaar is). Dezelfde playlist bewerken wordt actief na het huidige item of de
  lopende overgang, zodat niets halverwege wordt afgebroken.
- Elke asynchrone stap is gekoppeld aan een 'generatie'; verouderde stappen na een wijziging
  worden genegeerd, zodat er geen raceconditions ontstaan.

**Overige keuzes**

- **Python + Flask + waitress + SQLite** uit de standaardpakketten van Raspberry Pi OS:
  lichtgewicht, geen buildstap, geen pip of npm op de Pi nodig.
- **Beheerwebsite zonder buildstap**: Preact + htm en SortableJS, lokaal meegeleverd in
  `app/signage/static/vendor/` (met licenties). Geen CDN, geen externe fonts.
- **Systeemacties** (hostnaam, tijd, herstart) lopen via één klein root-programma
  `/usr/local/sbin/signage-priv` met gecontroleerde argumenten. Er is geen manier om via de
  website shellcommando's te draaien.
- **Beveiliging**: wachtwoorden gehasht (PBKDF2-SHA256, 300.000 rondes), sessiecookie
  HttpOnly/SameSite, CSRF-token bij elke wijziging, vertraging na mislukte logins, uploads
  alleen als ze echt een afbeelding, video of veilig HTML-project zijn. Voorvertoningen van
  HTML in de beheerwebsite draaien in een afgeschermde sandbox zonder toegang tot de beheer-API.

---

## 7. Offline werking

- De server en player maken **geen** verbindingen naar buiten. Ze gebruiken geen
  cloudaccount, licentieserver, externe API of CDN. Chromium wordt gestart zonder
  achtergrondverkeer (updates, sync, vertaling e.d. uit).
- Afspelen hangt niet af van de klok of NTP. Zonder internet loopt de klok door vanaf de
  laatst opgeslagen tijd (de Pi heeft geen batterijklok). Je kunt de tijd handmatig zetten
  onder *Instellingen → Tijd*.
- Systeemupdates (`apt`) hebben wel internet nodig; die zijn geen onderdeel van de normale
  werking. HTML-pagina's met externe bronnen: zie §4.

---

## 8. Betrouwbaarheid

- **Automatisch starten**: beide services starten bij het opstarten (systemd).
- **Na stroomuitval**: de player speelt direct de laatst bekende geldige playlist uit de cache,
  nog voordat de server klaar is.
- **Herstel**: server `Restart=on-failure`, player `Restart=always` met een systemd-watchdog
  (60 s). De daemon bewaakt mpv of Chromium: proces gestopt, playerpagina reageert niet meer,
  of te weinig vrij geheugen → herstart, met een afkoelperiode na 5 herstarts in 10 minuten.
  De services stoppen na 10 pogingen in 10 minuten, zodat een hardwareprobleem zichtbaar blijft.
- **Browserproblemen**: drie mislukte starts van de browserweergave → automatisch mpv.
  Terugzetten met *Instellingen → Weergave → Browserweergave opnieuw proberen* of `sudo signage-admin renderer reset`.
- **Hardware-watchdog** (`RuntimeWatchdogSec=15`): herstart de Pi als het hele systeem
  vastloopt. Uitschakelen: `sudo rm /etc/systemd/system.conf.d/signage-watchdog.conf` en herstart.
- **Kapotte bestanden en pagina's**: worden overgeslagen en gemeld; een vastgelopen
  HTML-pagina wordt na zijn duur gewoon vervangen. Is niets afspeelbaar, dan verschijnt het
  standby-scherm en wordt het elke 30 s opnieuw geprobeerd.
- **Geen halve gegevens**: uploads en ZIP-projecten worden eerst in een tijdelijke map
  verwerkt en pas na controle op hun plek gezet; restanten worden bij het opstarten opgeruimd.
  Configuratie- en cachebestanden worden atomair geschreven. De database gebruikt WAL.
- **SD-kaartslijtage**: de heartbeat van de player staat alleen in het geheugen, er wordt niet
  per verzoek gelogd en het systeemlogboek is begrensd tot 64 MB.
- **Opslag**: uploads worden geweigerd als er minder dan 500 MB vrij zou blijven.

---

## 9. Back-up en herstel

- **Website**: *Instellingen → Back-up*. *Back-up maken* (database en instellingen) of
  *Back-up met media maken* (inclusief HTML-projecten). Download back-ups naar je computer.
- **Automatisch**: elke nacht om ±03:30 een back-up zonder media (laatste 14 bewaard), en
  altijd een back-up vlak vóór een databasemigratie (bij een update) of een herstel.
- **Terugzetten**: kies een back-up in de lijst of upload een back-upbestand. Het archief
  wordt eerst volledig gecontroleerd, er wordt een veiligheidsback-up gemaakt en daarna
  herstart de server met de teruggezette database.
- **Opdrachtregel**:

```bash
sudo signage-admin backup                                  # zonder media
sudo signage-admin backup --with-media --output /media/<usb-stick>
sudo signage-admin restore /pad/naar/signage-backup-....tar.gz
```

---

## 10. Toegang herstellen

| Probleem | Oplossing |
|---|---|
| Wachtwoord vergeten (SSH werkt) | `sudo signage-admin reset-password` |
| Beheerder opnieuw aanmaken (SSH werkt) | `sudo signage-admin reset-admin` → nieuwe setup-code op het scherm |
| Geen SSH-toegang | Zet een leeg bestand **`signage-reset-admin`** in de hoofdmap van de SD-kaart (bootfs) en start de Pi. De beheerder wordt gewist en er verschijnt een nieuwe setup-code op het scherm. |
| Hostnaam werkt niet meer | Gebruik het IP-adres (standby-scherm of router). |
| Verkeerde poort ingesteld | Pas `port` aan in `/etc/signage/signage.ini` en `server_url` in `/etc/signage/player.ini`, dan `sudo signage-admin restart`. |
| Inloggen op de Pi zelf | De player gebruikt de eerste console (tty1). Druk **Ctrl+Alt+F2** voor een inlogscherm; terug met Ctrl+Alt+F1. |

---

## 11. Updaten en terugdraaien

1. Kopieer de nieuwe map `signage` naar de Pi (via de SD-kaart of `scp`).
2. `sudo bash /boot/firmware/signage/install.sh`

Het script is veilig opnieuw uit te voeren: configuratie, database, media en setup-code
blijven behouden, er ontstaan geen dubbele services. Moet de database worden bijgewerkt (zoals
van 1.0 naar 1.1), dan maakt de server eerst automatisch een back-up
(`signage-backup-pre-migration-…`) en migreert daarna in één transactie.

Terugdraaien naar de vorige softwareversie: `sudo signage-admin rollback`. Is er tussendoor
een databasemigratie geweest, zet dan eerst de pre-migration-back-up terug
(`sudo signage-admin restore /var/lib/signage/backups/signage-backup-pre-migration-….tar.gz`)
en draai daarna `rollback`.

Systeempakketten bijwerken (internet nodig): `sudo apt update && sudo apt full-upgrade`, daarna `sudo reboot`.

### Van 1.0 naar 1.1

- Bestaande playlists blijven werken: overgangen staan uit, de schaalmodus is *Vullen*.
- Afspeelversies van foto's worden eenmalig opnieuw gemaakt voor beeldvullende weergave.
- De installer installeert cage en Chromium en zet de player op een eigen sessie op tty1.
- Let op: *Vullen* is nu de standaard. Wil je het oude gedrag (zwarte randen bij afwijkende
  verhoudingen), kies dan *Passend* onder *Instellingen → Weergave*.

---

## 12. Meerdere schermen

De architectuur ondersteunt meerdere players: *Schermen → Externe player toevoegen* geeft een
token en de installatieopdracht voor een tweede Pi:

```bash
sudo bash /boot/firmware/signage/install.sh --role player --server http://<ip-van-de-server> --token <token>
```

Een externe player downloadt de media en HTML-projecten naar zijn eigen SD-kaart (met
SHA-256-controle), speelt door als het netwerk wegvalt en volgt de standaardplaylist of een
eigen playlist. Het downloaden is getest; een tweede fysieke Pi niet.

---

## 13. Configuratie

| Bestand | Inhoud |
|---|---|
| `/etc/signage/signage.ini` | Poort, adres, opslaglimieten, afbeeldingsformaat, sessieduur. Na wijzigen: `sudo systemctl restart signage-server` |
| `/etc/signage/player.ini` | Serveradres, player-token, weergave (`renderer`), Chromium- en mpv-opties. Na wijzigen: `sudo systemctl restart signage-player` |

Elke optie is in het bestand zelf gedocumenteerd. Weergave kiezen zonder bestand te bewerken:
*Instellingen → Weergave* in de beheerwebsite, of `sudo signage-admin renderer auto|browser|mpv` (en `reset`).

Opties van het installatiescript: `sudo bash install.sh --help`
(`--role`, `--server`, `--token`, `--port`, `--offline`, `--disable-desktop`, `--no-watchdog`,
`--no-gpu-mem`, `--no-browser`).

Wijzigingen die het script aan het systeem doet (allemaal omkeerbaar):
- pakketten installeren (alleen wat ontbreekt);
- gebruiker `signage` (groepen video, render, audio, input, systemd-journal);
- `/etc/sudoers.d/signage` (alleen `signage-priv`);
- `/etc/pam.d/signage-player` (sessie voor de player op tty1; de login-prompt op tty1 maakt
  plaats voor de player, tty2 blijft beschikbaar);
- `/etc/systemd/journald.conf.d/signage.conf` (logboek max. 64 MB);
- `/etc/systemd/system.conf.d/signage-watchdog.conf` (tenzij `--no-watchdog`);
- op een Pi 3: `gpu_mem=128` in `config.txt` als daar nog geen `gpu_mem` staat (back-up van
  `config.txt` wordt gemaakt; tenzij `--no-gpu-mem`);
- met `--disable-desktop`: opstarten zonder desktop (`systemctl set-default multi-user.target`).

---

## 14. Bestanden en mappen

```
/opt/signage/releases/<versie>/   software (root, alleen-lezen voor signage)
/opt/signage/current              actieve versie;  /opt/signage/previous = vorige
/etc/signage/                     configuratie
/var/lib/signage/signage.db       database
/var/lib/signage/media/files/     originele uploads (ook .html/.zip)
/var/lib/signage/media/renditions/ afspeelversies van foto's
/var/lib/signage/media/html/      uitgepakte HTML-projecten (één map per project)
/var/lib/signage/media/thumbs/    miniaturen
/var/lib/signage/backups/         back-ups
/var/lib/signage/player/          cache van de player (+ Chromium-profiel)
/usr/local/sbin/signage-priv      systeemacties (root)
/usr/local/bin/signage-admin      opdrachtregelbeheer
/var/log/signage-install.log      logboek van het installatiescript
```

Projectmap (dit pakket):

```
install.sh  uninstall.sh  VERSION  README.md  CHANGELOG.md
app/signage/                 backend (Python)
app/signage/player/          player: synchronisatie, browser- en mpv-weergave
app/signage/static/          beheerwebsite, player/ (playerpagina), display/ (lokale pagina's)
bin/                         signage-priv, signage-admin, signage-player-launch
config/                      signage.ini, player.ini, sudoers- en PAM-regel (sjablonen)
systemd/                     unit-bestanden
tests/                       automatische tests (zie §17)
```

---

## 15. Problemen oplossen

```bash
sudo signage-admin status          # versie, services, beheerder
sudo signage-admin renderer        # actieve weergave en mislukte browserstarts
sudo signage-admin logs            # live logboek van server en player
journalctl -u signage-player -n 100
```

| Symptoom | Oorzaak / oplossing |
|---|---|
| Zwart scherm | Scherm uit tijdens het opstarten, of er draait een desktop. Gebruik Lite, of installeer met `--disable-desktop`. Bekijk `journalctl -u signage-player`. |
| Dashboard: "teruggevallen op mpv" | De browserweergave startte niet. Bekijk het logboek, los het op en voer `sudo signage-admin renderer reset` uit. |
| HTML-items worden overgeslagen | De mpv-weergave is actief (zie hierboven), of de pagina ontbreekt. |
| Scherm wordt niet herkend als het later aangaat | Voeg aan het einde van de regel in `bootfs/cmdline.txt` toe: ` video=HDMI-A-1:1920x1080@60D` en herstart. |
| Video hapert | Controleer het formaat (§5) en het dashboard (overgeslagen frames). Probeer 720p, of `sudo signage-admin renderer mpv`. |
| Pagina met vaste opmaak valt buiten beeld | Stel bij de pagina het ontwerpformaat in (§4). |
| Geen geluid via HDMI | mpv: zet `audio_device` in `player.ini` (`mpv --audio-device=help`). |
| Dashboard meldt onderspanning | Gebruik de officiële voeding (5,1 V / 2,5 A). |
| Website niet bereikbaar | Controleer het IP-adres; `systemctl status signage-server`; poort 80 bezet? |

---

## 16. Terug naar PiSignage / verwijderen

- **Terug naar PiSignage**: stop de oude SD-kaart terug. Die is niet aangeraakt.
- **Media uit PiSignage overnemen**: kopieer de mediabestanden van de oude kaart naar je
  computer en upload ze via *Media*.
- **Verwijderen**: `sudo bash /boot/firmware/signage/uninstall.sh` verwijdert services en
  software en bewaart gegevens; met `--purge` ook gegevens, configuratie en gebruiker.
  Systeempakketten (mpv, Chromium, …) blijven staan.

---

## 17. Testverslag en acceptatiecriteria

### Hoe er getest is

Er was geen toegang tot een Raspberry Pi of het aangesloten scherm. Getest is in een
Linux-testomgeving (x86_64, Python 3.13 zoals Raspberry Pi OS Trixie):

1. **Automatische tests** — `python3 -m pytest tests/` — **72 tests, alle geslaagd**:
   - `test_api.py` (27): login, CSRF, uploads, playlists, player-API, back-up/herstel;
   - `test_v11.py` (15): migratie van een echte 1.0-database, HTML-bestanden en ZIP's
     (inclusief path traversal, absolute paden, symlinks, verboden types, ZIP-bom),
     ingebouwde pagina's, ontwerpformaat, overgangen, schaalmodi, verwijderen van een
     overgangsvideo, externe player die een HTML-project downloadt, beveiliging en
     Range-verzoeken van de lokale playerserver;
   - `test_player.py` (11): mpv-weergave tegen een nagebootste mpv (volgorde, kapotte en
     ontbrekende bestanden, standby, vastgelopen mpv, overgangen, schaalmodi, HTML overslaan,
     offline doorspelen, cache na herstart);
   - `test_browser_player.py` (19): de echte playerpagina in Chromium (headless) met
     schermafbeeldingen en pixelmetingen.
2. **Browserweergave** in Chromium: schaalmodi voor foto, video en HTML (vullen = geen zwart en
   proportioneel, passend = zwarte randen, uitrekken = vervormd), kleine foto opgeschaald,
   HTML vult het venster, HTML met vast ontwerpformaat in alle drie modi, schermrotatie 90°,
   alle 9 soorten wisselingen (foto/video/HTML onderling) **met en zonder** overgang over twee
   volledige rondes: **0 frames zonder zichtbare, geladen laag**, de overgang is tijdens het
   afspelen steeds de bovenste laag en speelt volledig (≥ 1,0 s van 1,2 s) voordat het volgende
   item begint, nooit twee overgangen achter elkaar. Verder: kapotte/ontbrekende overgang
   overgeslagen, vastgelopen HTML-pagina blokkeert niets, HTML met externe bronnen offline,
   bewerken tijdens een overgang (overgang wordt afgemaakt), direct wisselen naar een andere
   playlist, lege playlist → standby → herstel.
3. **Echte mpv 0.37** op een virtueel scherm (Xvfb): schaalmodi visueel gecontroleerd voor
   foto en video (`tests/mpv_visual_check.py`).
4. **Volledige keten** `signage-player-launch` → cage (headless) → player-daemon → Chromium-kiosk
   met de playlist uit de beheerwebsite: foto, HTML-ZIP, klokpagina en overgangen speelden af;
   het venster meldde de schermgrootte. Ook de automatische terugval naar mpv na drie mislukte
   starts is getest.
5. **Echte installatie en upgrade** met `install.sh` (systemctl nagebootst): 1.0.0 → 1.1.0 met
   bestaande gegevens: pre-migration-back-up, schema 2, playlists behouden, afspeelversies
   bijgewerkt, database intact; daarna `tests/check_installed_v11.py` met de echte mpv: 13/13.
   Uit 1.0: installatie, herinstallatie, `--offline`, rollback, uninstall, harde kill
   (player speelt binnen seconden verder uit de cache), wachtwoordherstel via de SD-kaart,
   duurtest (geheugen stabiel).
6. **Beheerwebsite** in Chromium: alle 1.0-stappen (22) plus de 1.1-stappen (HTML-upload,
   lokale pagina, ontwerpformaat, overgangen instellen, per item geen/eigen overgang,
   schaalmodus per item, opslaan en herladen, voorbeeld met overgang, globale schaalmodus);
   mobiel zonder horizontaal scrollen; geen enkele externe netwerkaanvraag.

Fouten die deze tests vonden en die zijn opgelost: een vastgelopen HTML-pagina bevroor in
Chromium ook de player (opgelost met procesisolatie per site), de tweede video in een playlist
bleef in mpv gepauzeerd staan, de installatiemap kreeg de Debian-versienaam, bestanden die je
kiest vóór de mediapagina geladen was werden genegeerd, en de decoder werd niet gemeld bij
overgangsvideo's.

### Acceptatiecriteria 1.1

| # | Criterium | Status |
|---|---|---|
| 1 | Foto's beeldvullend | Pixelmetingen in Chromium en echte mpv: geslaagd. **Op het scherm van de Pi controleren.** |
| 2 | Video's beeldvullend | Idem (VP9 in de test; H.264 op de Pi). **Op de Pi controleren.** |
| 3 | HTML beeldvullend | Chromium: geslaagd (responsief en met ontwerpformaat). **Op de Pi controleren.** |
| 4 | Standaard geen zwarte randen | Vullen is de standaard; gemeten zonder zwart. |
| 5 | Verhouding behouden bij Vullen | Gemeten (banden van het testbeeld vallen weg, niets vervormd). |
| 6 | Passend werkt | Gemeten (zwarte zijkanten, volledige afbeelding zichtbaar). |
| 7 | Uitrekken werkt | Gemeten (banden over volle breedte). |
| 8 | Overgang volledig tussen twee items | Gemeten in Chromium (≥ 1,0 s van 1,2 s, steeds bovenste laag); echte mpv: overgang → item → overgang waargenomen. |
| 9 | Playlist zonder overgangen werkt normaal | Getest (alle wisselingen, 0 lege frames). |
| 10 | Ontbrekende/kapotte overgang blokkeert niet | Getest in beide weergaven. |
| 11 | HTML werkt zonder internet | Getest, ook met externe verwijzingen. |
| 12 | HTML gaat door na de ingestelde tijd | Getest, ook bij een vastgelopen pagina. |
| 13 | Mix van foto's, video's en HTML | Getest (10 items, twee rondes). |
| 14 | Configuratie blijft na reboot | Alles in SQLite en `/etc/signage`; geen reboot op de Pi getest. **Op de Pi controleren.** |
| 15 | Stabiel bij langdurig afspelen | 1.0: duurtest met echte mpv, geheugen stabiel. Chromium op de Pi niet getest. **Laat de Pi een paar dagen draaien.** |

De acceptatiecriteria van 1.0 (website, upload, playlists, back-up, herstel na kill, enz.)
zijn opnieuw getest en geslaagd; zie `CHANGELOG.md`.

### Controle op de Pi (± 30 minuten)

1. Na installatie en `sudo reboot`: verschijnt het standby-scherm met setup-code?
   `sudo signage-admin renderer` moet `auto` en 0 mislukte starts tonen.
2. Beheerder aanmaken via `http://signage.local` én via het IP-adres.
3. Upload een staande foto, een liggende foto, een korte MP4 (H.264) en een HTML-ZIP. Maak een
   playlist met alle vier, zet overgangen aan met een korte MP4 als overgang, *Opslaan en
   activeren*. Kijk: geen zwarte randen (Vullen), geen zwart beeld tussen items, de overgang
   volledig tussen elk item, de HTML-pagina vult het scherm.
4. Schakel onder *Instellingen → Weergave* naar *Passend* en *Uitrekken* en kijk het verschil.
5. Dashboard: hoeveel procent videoframes wordt overgeslagen? (Boven ~5% → 720p-video's.)
6. Haal de internetverbinding weg (lokaal netwerk blijft): werkt alles nog?
7. Trek de stekker eruit en weer erin: komt de playlist vanzelf terug?

---

## 18. Bekende beperkingen

- **Niet getest op echte Pi-hardware en niet op het fysieke scherm.** De beeldvulling is met
  schermafbeeldingen en pixelmetingen in Chromium en mpv gecontroleerd, niet visueel op het
  aangesloten scherm. Op de Pi moeten nog worden gecontroleerd: cage met een logind-sessie op
  tty1, Chromium op 1 GB RAM, video- en hardwaredecodering in Chromium op de Pi 3B+, HDMI-geluid,
  `gpu_mem=128`, de hardware-watchdog en een echte reboot.
- **Chromium op een Pi 3B+** is zwaar. Of H.264 in Chromium hardwarematig wordt gedecodeerd is
  onzeker; het dashboard toont het percentage overgeslagen frames. De mpv-weergave decodeert
  wel hardwarematig, maar toont geen HTML.
- **mpv-weergave**: bij het wisselen kan heel kort het laatste frame of een zwart frame te zien
  zijn; er is geen voorladen zoals in de browserweergave.
- **Voorvertoning in de beheerwebsite** gebruikt de browser van je eigen apparaat (en dus zijn
  videocodecs); het echte scherm kan iets andere timing hebben.
- **De bestaande PiSignage is niet onderzocht** (geen toegang tot de Pi; nieuwe SD-kaart).
  Er is niets gemigreerd; media moeten opnieuw worden geüpload.
- HTML-pagina's kunnen geen geluid laten horen tenzij ze zelf media afspelen; interactie
  (aanraken, toetsenbord) wordt niet ondersteund.
- Eén beheeraccount, geen tijdschema's, HTTP zonder TLS (vertrouwd lokaal netwerk).

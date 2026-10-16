# Signage — volledig lokaal digital-signage-systeem voor de Raspberry Pi 3B+

Eén Raspberry Pi is tegelijk **server**, **beheeromgeving** en **player**:

- een beheerwebsite (computer, tablet, telefoon) voor media, playlists, schermen en instellingen;
- een player die zonder desktop direct op het HDMI-scherm afspeelt;
- alles lokaal: SQLite-database, media op de SD-kaart, geen cloud, geen externe fonts of scripts.

Na de installatie is **geen internet** meer nodig. Alleen de eerste installatie (en updates
van systeempakketten) downloadt pakketten.

---

## Inhoud

1. [Installatie](#1-installatie)
2. [De beheerwebsite bereiken](#2-de-beheerwebsite-bereiken)
3. [Dagelijks gebruik](#3-dagelijks-gebruik)
4. [Ondersteunde formaten](#4-ondersteunde-formaten-pi-3b)
5. [Hoe het werkt](#5-hoe-het-werkt)
6. [Offline werking](#6-offline-werking)
7. [Betrouwbaarheid](#7-betrouwbaarheid)
8. [Back-up en herstel](#8-back-up-en-herstel)
9. [Toegang herstellen](#9-toegang-herstellen)
10. [Updaten en terugdraaien](#10-updaten-en-terugdraaien)
11. [Meerdere schermen](#11-meerdere-schermen)
12. [Configuratie](#12-configuratie)
13. [Bestanden en mappen](#13-bestanden-en-mappen)
14. [Problemen oplossen](#14-problemen-oplossen)
15. [Terug naar PiSignage / verwijderen](#15-terug-naar-pisignage--verwijderen)
16. [Testverslag en acceptatiecriteria](#16-testverslag-en-acceptatiecriteria)
17. [Bekende beperkingen](#17-bekende-beperkingen)

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
2. Pak `signage-1.0.0.zip` uit en kopieer de map **`signage`** naar de hoofdmap van bootfs
   (dus `bootfs/signage/install.sh`).
3. Werp de kaart netjes uit en stop hem in de Pi. Sluit scherm en netwerk aan en zet de Pi aan.
   De eerste start duurt een paar minuten.

### Stap 3 — Installeren (één opdracht)

Log in via SSH vanaf je computer (of met toetsenbord op de Pi zelf):

```bash
ssh <gebruikersnaam>@signage.local
sudo bash /boot/firmware/signage/install.sh
```

Het script controleert het systeem, installeert de benodigde pakketten (`mpv`, `ffmpeg`,
`python3-flask`, `python3-waitress`, `python3-pil`, `fonts-dejavu-core`, `avahi-daemon`),
maakt een systeemgebruiker `signage`, zet de services klaar en start ze. Aan het einde toont
het de adressen van de beheerwebsite. Vraagt het om een herstart (`gpu_mem` of watchdog),
doe dan `sudo reboot`.

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
van de playlist), status van server en player, vrije opslag, uptime, temperatuur, meldingen
en de belangrijkste instellingen. Wordt elke paar seconden bijgewerkt zonder herladen. Je kunt
hier direct een andere playlist kiezen.

**Media** — sleep bestanden in het venster of kies ze; meerdere tegelijk, met voortgang.
Elk bestand wordt gecontroleerd (echt een afbeelding/video? codec? resolutie?). Zoeken,
filteren, sorteren, voorvertonen, hernoemen, origineel downloaden en verwijderen. Een bestand
dat in een playlist staat kan alleen na een extra waarschuwing worden verwijderd.

**Playlists** — media toevoegen, volgorde slepen (of met pijltjestoetsen op het
greepje), duur per afbeelding (leeg = standaardduur), herhalen:
*steeds herhalen*, *één keer en laatste item laten staan* of *één keer en dan de
standaardplaylist*; geluid van video's aan/uit. *Opslaan en activeren* zet de playlist binnen
enkele seconden op het scherm, zonder herstart. Een lege playlist of een playlist met
ontbrekende bestanden kan niet worden geactiveerd. *Voorbeeld* speelt de playlist in je browser.

**Schermen** — de server, de players en het scherm per player: online-status, laatste
contact, wat er speelt, of de HDMI-aansluiting een scherm ziet en met welke resolutie,
schermstand (liggend/staand/ondersteboven) en welke playlist de player volgt.

**Instellingen** — systeemnaam, standaardplaylist, standaardduur, volume, player
herstarten/uitzetten, hostnaam, tijdzone, tijdsynchronisatie, tijd handmatig instellen,
back-ups, logboeken, systeeminformatie, herstart, wachtwoord.

---

## 4. Ondersteunde formaten (Pi 3B+)

| Soort | Werkt betrouwbaar | Toelichting |
|---|---|---|
| Afbeelding | PNG, JPEG, WebP | Worden automatisch verkleind tot 1920×1080 en rechtop gezet (EXIF). Max. 40 megapixel. |
| Video | **MP4 met H.264 (8-bit, yuv420p) + AAC**, tot **1920×1080 @ 30 fps** | Hardwarematig gedecodeerd. |
| Video, met waarschuwing | H.265/HEVC, VP9, AV1, 10-bit H.264, >1080p of 60 fps bij 1080p | Worden geaccepteerd met een waarschuwing; de Pi 3B+ decodeert dit in software en dat hapert of lukt niet. |
| Geweigerd | Alles wat geen echte afbeelding/video is (ook bij een misleidende extensie) | — |

Een video omzetten naar het aanbevolen formaat (op je computer, met ffmpeg):

```bash
ffmpeg -i invoer.mov -c:v libx264 -profile:v high -level 4.0 -pix_fmt yuv420p \
  -vf "scale='min(1920,iw)':-2,fps=30" -c:a aac -b:a 128k -movflags +faststart uitvoer.mp4
```

---

## 5. Hoe het werkt

```
 Browser (pc/tablet/telefoon)            Raspberry Pi 3B+
 ─────────────────────────────    ┌───────────────────────────────────────────────┐
                                  │ signage-server.service  (gebruiker signage)    │
   beheerwebsite  ── HTTP :80 ──▶ │  Flask + waitress · SQLite · media op SD       │
                                  │  /api/...            beheer-API (login)        │
                                  │  /api/player/v1/...  player-API (token)        │
                                  └───────────────▲───────────────────────────────┘
                                                  │ HTTP (localhost), elke 5 s,
                                                  │ met ETag; heartbeat elke 15 s
                                  ┌───────────────┴───────────────────────────────┐
                                  │ signage-player.service  (gebruiker signage)    │
                                  │  Python-daemon ── JSON-IPC ──▶ mpv             │
                                  │  cache: laatst geldige playlist               │──▶ HDMI
                                  └───────────────────────────────────────────────┘
```

**Keuzes en waarom**

- **Geen desktop, geen browser als player.** mpv tekent via DRM/KMS direct op het scherm en
  gebruikt de H.264-decoder van de Pi. Chromium in kioskmodus is op 1 GB RAM zwaar en
  minder voorspelbaar bij video. Gevolg: geen muiscursor of interface-elementen.
- **Python + Flask + waitress + SQLite** uit de standaardpakketten van Raspberry Pi OS:
  lichtgewicht, geen buildstap, geen pip of npm op de Pi nodig, eenvoudig te onderhouden.
- **Frontend zonder buildstap**: Preact + htm (13 KB) en SortableJS, lokaal meegeleverd in
  `app/signage/static/vendor/` (met licenties). Geen CDN, geen externe fonts.
- **Server en player zijn gescheiden services** die via HTTP praten. Dezelfde player kan
  daardoor ook op een andere Pi draaien (zie §11). De webinterface sluiten heeft geen invloed
  op het afspelen.
- **Systeemacties** (hostnaam, tijd, herstart) lopen via één klein root-programma
  `/usr/local/sbin/signage-priv` dat alleen vooraf bepaalde acties met gecontroleerde
  argumenten uitvoert. Er is geen manier om via de website shellcommando's te draaien.
- **Beveiliging**: wachtwoorden gehasht (PBKDF2-SHA256, 300.000 rondes), sessiecookie
  HttpOnly/SameSite, CSRF-token bij elke wijziging, oplopende vertraging na mislukte logins,
  uploads alleen als ze echt een afbeelding of video zijn, veilige bestandsnamen (UUID's).

---

## 6. Offline werking

- De server en player maken **geen** verbindingen naar buiten. Ze gebruiken geen
  cloudaccount, licentieserver, externe API of CDN.
- Afspelen hangt niet af van de klok of NTP. Zonder internet loopt de klok door vanaf de
  laatst opgeslagen tijd (de Pi heeft geen batterijklok). Je kunt de tijd handmatig zetten
  onder *Instellingen → Tijd*. Tijdsynchronisatie (systemd-timesyncd) blijft optioneel aan en
  blokkeert niets als er geen internet is.
- Systeemupdates (`apt`) hebben wel internet nodig; die zijn geen onderdeel van de normale
  werking.

---

## 7. Betrouwbaarheid

- **Automatisch starten**: beide services starten bij het opstarten (systemd).
- **Na stroomuitval**: de player speelt direct de laatst bekende geldige playlist uit
  `/var/lib/signage/player/last-config.json`, nog voordat de server klaar is.
- **Herstel**: server `Restart=on-failure`, player `Restart=always` met een
  systemd-watchdog (60 s). Binnen de player wordt mpv gecontroleerd; reageert het niet, dan
  wordt het herstart. Na 5 herstarts in 10 minuten volgt een afkoelperiode van 60 s in
  plaats van een agressieve lus. De services stoppen met herstarten na 10 pogingen in
  10 minuten, zodat een hardwareprobleem zichtbaar blijft in het dashboard en logboek.
- **Hardware-watchdog** (`RuntimeWatchdogSec=15`): herstart de Pi als het hele systeem
  vastloopt. Uitschakelen: `sudo rm /etc/systemd/system.conf.d/signage-watchdog.conf` en herstart.
- **Kapotte bestanden**: een ontbrekend of onleesbaar bestand wordt overgeslagen en gemeld;
  is niets afspeelbaar, dan verschijnt het standby-scherm en wordt het elke 30 s opnieuw
  geprobeerd.
- **Geen halve gegevens**: uploads worden eerst in een tijdelijke map ontvangen en pas na
  controle verplaatst; restanten worden bij het opstarten opgeruimd. Configuratie- en
  cachebestanden worden atomair geschreven. De database gebruikt WAL.
- **SD-kaartslijtage**: de heartbeat van de player staat alleen in het geheugen (laatste
  contact wordt hooguit elke 5 minuten weggeschreven), er wordt niet per verzoek gelogd en
  het systeemlogboek is begrensd tot 64 MB.
- **Opslag**: uploads worden geweigerd als er minder dan 500 MB vrij zou blijven; het
  dashboard waarschuwt eerder.

---

## 8. Back-up en herstel

- **Website**: *Instellingen → Back-up*. *Back-up maken* (database en instellingen) of
  *Back-up met media maken*. Download back-ups naar je computer: een kapotte SD-kaart neemt
  de back-ups op de kaart mee.
- **Automatisch**: elke nacht om ±03:30 een back-up zonder media; de laatste 14 blijven bewaard.
- **Terugzetten**: kies een back-up in de lijst of upload een back-upbestand. Het archief
  wordt eerst volledig gecontroleerd, er wordt een *veiligheidsback-up* van de huidige stand
  gemaakt en daarna herstart de server met de teruggezette database.
- **Opdrachtregel**:

```bash
sudo signage-admin backup                                  # zonder media
sudo signage-admin backup --with-media --output /media/<usb-stick>
sudo signage-admin restore /pad/naar/signage-backup-....tar.gz
```

`/etc/signage/signage.ini` zit ter referentie in de back-up maar wordt niet automatisch
teruggezet.

---

## 9. Toegang herstellen

| Probleem | Oplossing |
|---|---|
| Wachtwoord vergeten (SSH werkt) | `sudo signage-admin reset-password` |
| Beheerder opnieuw aanmaken (SSH werkt) | `sudo signage-admin reset-admin` → nieuwe setup-code op het scherm |
| Geen SSH-toegang | Zet een leeg bestand **`signage-reset-admin`** in de hoofdmap van de SD-kaart (bootfs) en start de Pi. De beheerder wordt gewist en er verschijnt een nieuwe setup-code op het scherm. |
| Hostnaam werkt niet meer | Gebruik het IP-adres (staat op het standby-scherm, of in je router). |
| Verkeerde poort ingesteld | Pas `port` aan in `/etc/signage/signage.ini` en `server_url` in `/etc/signage/player.ini`, dan `sudo signage-admin restart`. |

---

## 10. Updaten en terugdraaien

1. Kopieer de nieuwe map `signage` naar de Pi (bijvoorbeeld via de SD-kaart of `scp`).
2. `sudo bash /boot/firmware/signage/install.sh`

Het script is veilig opnieuw uit te voeren: configuratie, database, media en setup-code
blijven behouden, er ontstaan geen dubbele services. Elke versie staat in
`/opt/signage/releases/<versie>`; `/opt/signage/current` wijst naar de actieve.

Terugdraaien naar de vorige versie: `sudo signage-admin rollback`.

Systeempakketten bijwerken (internet nodig): `sudo apt update && sudo apt full-upgrade`,
daarna `sudo reboot`.

---

## 11. Meerdere schermen

De architectuur ondersteunt meerdere players: *Schermen → Externe player toevoegen* geeft een
token en de installatieopdracht voor een tweede Pi:

```bash
sudo bash /boot/firmware/signage/install.sh --role player --server http://<ip-van-de-server> --token <token>
```

Een externe player downloadt de media naar zijn eigen SD-kaart (met controle via SHA-256),
speelt door als het netwerk wegvalt en volgt de standaardplaylist of een eigen playlist.
Het downloaden is getest; een tweede fysieke Pi is niet getest.

---

## 12. Configuratie

| Bestand | Inhoud |
|---|---|
| `/etc/signage/signage.ini` | Poort, adres, opslaglimieten, afbeeldingsformaat, sessieduur. Elke optie is gedocumenteerd in het bestand. Na wijzigen: `sudo systemctl restart signage-server` |
| `/etc/signage/player.ini` | Serveradres, player-token, mpv-opties (decodering, audio-uitgang). Na wijzigen: `sudo systemctl restart signage-player` |

Opties van het installatiescript: `sudo bash install.sh --help`
(`--role`, `--server`, `--token`, `--port`, `--offline`, `--disable-desktop`, `--no-watchdog`, `--no-gpu-mem`).

Wijzigingen die het script aan het systeem doet (allemaal omkeerbaar):
- pakketten installeren (alleen wat ontbreekt);
- gebruiker `signage` (groepen video, render, audio, input, systemd-journal);
- `/etc/sudoers.d/signage` (alleen `signage-priv`);
- `/etc/systemd/journald.conf.d/signage.conf` (logboek max. 64 MB);
- `/etc/systemd/system.conf.d/signage-watchdog.conf` (tenzij `--no-watchdog`);
- op een Pi 3: `gpu_mem=128` in `config.txt` als daar nog geen `gpu_mem` staat (back-up van
  `config.txt` wordt gemaakt; tenzij `--no-gpu-mem`);
- met `--disable-desktop`: opstarten zonder desktop (`systemctl set-default multi-user.target`).

---

## 13. Bestanden en mappen

```
/opt/signage/releases/<versie>/   software (root, alleen-lezen voor signage)
/opt/signage/current              actieve versie;  /opt/signage/previous = vorige
/etc/signage/                     configuratie
/var/lib/signage/signage.db       database
/var/lib/signage/media/files/     originele uploads
/var/lib/signage/media/renditions/ afspeelversies van afbeeldingen
/var/lib/signage/media/thumbs/    miniaturen
/var/lib/signage/backups/         back-ups
/var/lib/signage/player/          cache van de player
/usr/local/sbin/signage-priv      systeemacties (root)
/usr/local/bin/signage-admin      opdrachtregelbeheer
/var/log/signage-install.log      logboek van het installatiescript
```

Projectmap (dit pakket):

```
install.sh  uninstall.sh  VERSION  README.md
app/signage/        backend (Python) + static/ (beheerwebsite)
app/signage/player/ player-daemon
bin/                signage-priv, signage-admin
config/             signage.ini, player.ini, sudoers-regel (sjablonen)
systemd/            unit-bestanden
tests/              automatische tests (zie §16)
```

---

## 14. Problemen oplossen

```bash
sudo signage-admin status          # versie, services, beheerder
sudo signage-admin logs            # live logboek van server en player
journalctl -u signage-player -n 100
```

| Symptoom | Oorzaak / oplossing |
|---|---|
| Zwart scherm, player meldt `mpv stopte direct` | Scherm niet aangesloten of uit tijdens het opstarten, of er draait een desktop. Gebruik Lite, of installeer met `--disable-desktop`. |
| Scherm wordt niet herkend als het later aangaat | Voeg aan het einde van de regel in `bootfs/cmdline.txt` toe: ` video=HDMI-A-1:1920x1080@60D` en herstart. |
| Video hapert | Controleer het formaat (§4). Staat `gpu_mem=128` in `config.txt`? Bij beeldfouten: `hwdec = no` in `player.ini`. |
| Geen geluid via HDMI | Zet `audio_device` in `player.ini`; beschikbare apparaten: `mpv --audio-device=help`. |
| Dashboard meldt onderspanning | Gebruik de officiële voeding (5,1 V / 2,5 A). |
| Website niet bereikbaar | Controleer het IP-adres; `systemctl status signage-server`; poort 80 bezet door iets anders? |

---

## 15. Terug naar PiSignage / verwijderen

- **Terug naar PiSignage**: stop de oude SD-kaart terug. Die is niet aangeraakt.
- **Media uit PiSignage overnemen**: kopieer de mediabestanden van de oude kaart naar je
  computer en upload ze via *Media*. (De oude installatie is niet onderzocht: er was geen
  toegang tot de Pi, zie §17.)
- **Verwijderen**: `sudo bash /boot/firmware/signage/uninstall.sh` verwijdert services en
  software en bewaart gegevens; met `--purge` ook gegevens, configuratie en gebruiker.

---

## 16. Testverslag en acceptatiecriteria

### Hoe er getest is

Er was geen toegang tot een Raspberry Pi. Daarom is getest in een Linux-testomgeving
(x86_64, Python 3.13 zoals Raspberry Pi OS Trixie):

1. **Automatische tests** (`tests/test_api.py`, `tests/test_player.py`, 35 tests, alle
   geslaagd): authenticatie, CSRF, uploads (echte PNG/JPEG/WebP/H.264/HEVC, kapotte en
   misleidende bestanden, afgebroken uploads, padmanipulatie), playlists, player-API,
   back-up/herstel, en de player-logica tegen een nagebootste mpv (volgorde, kapotte en
   ontbrekende bestanden, standby, vastgelopen mpv, eenmalig afspelen, offline doorspelen,
   herstart uit cache, downloaden door een externe player).
   Uitvoeren: `python3 -m pytest tests/` (vereist pytest, flask, waitress, pillow, ffmpeg).
2. **Browsertest** van de beheerwebsite in Chromium: setup, foute setup-code, uploaden,
   ongeldig bestand, voorbeeld, zoeken, playlist maken, slepen, toetsenbord, opslaan en
   activeren, dashboard toont wat speelt, verwijderwaarschuwing, schermstand, instellingen,
   back-up, uitloggen; mobiele weergave (390 px) zonder horizontaal scrollen; donker thema.
   Geen enkele aanvraag naar een extern adres.
3. **Echte installatie** met `install.sh` (pakketten via apt, gebruiker, rechten, sudoers,
   releases), met een nagebootste `systemctl` omdat systemd in de testomgeving niet draait.
   Daarna `tests/check_installed.py` tegen het geïnstalleerde systeem met de **echte mpv
   0.37** (uitvoer naar `vo=null`): 21/21 geslaagd. Verder: herinstallatie behoudt alles,
   installatie met `--offline`, update naar een nieuwe versie en `rollback`, `uninstall.sh
   --purge`, harde kill van alle processen, wachtwoordherstel via de SD-kaart, CLI-back-up
   naar een externe map, en een duurtest.

Deze tests vonden drie fouten die op de Pi zouden zijn opgetreden en die zijn opgelost:
de installatiemap kreeg de Debian-versienaam (variabele uit `/etc/os-release`), de tweede
video in een playlist bleef gepauzeerd op het eerste frame (mpv-pauze blijft na
`--keep-open` staan), en events van mpv konden verloren gaan.

### Acceptatiecriteria

| # | Criterium | Status |
|---|---|---|
| 1 | Website opent vanaf ander apparaat | Server luistert op alle adressen en beantwoordt alleen lokale netwerken (getest met lokale en publieke afzenderadressen). **Op de Pi controleren.** |
| 2 | Werkt zonder internet | Geen uitgaande verbindingen gemeten; geen externe bronnen in de website; installatie met `--offline` werkt. **Echte test: internet loskoppelen op de Pi.** |
| 3 | Media uploaden, bekijken, beheren | Getest (API, browser, geïnstalleerd systeem). |
| 4 | Playlist maken, aanpassen, opslaan, activeren | Getest (API, browser, geïnstalleerd systeem). |
| 5 | Player speelt afbeeldingen en video | Logica getest met echte mpv (`vo=null`). **Beeld en hardwaredecodering op de Pi controleren.** |
| 6 | Hervat na herstart | Getest: na harde kill speelt de player binnen seconden uit de cache, ook zonder server. **Echte stroomonderbreking op de Pi testen.** |
| 7 | Automatisch starten na booten | Units `WantedBy=multi-user.target`, ingeschakeld door de installer. **Reboot op de Pi testen.** |
| 8 | Kapot bestand → geen crash | Getest met echte mpv en een beschadigd bestand op de opslag. |
| 9 | Mislukte upload → geen half bestand | Getest (afgebroken, ongeldig, gevaarlijk type). |
| 10 | Configuratie blijft na reboot | Alles staat in SQLite (WAL) en `/etc/signage`; integriteit na harde kill gecontroleerd. **Reboot op de Pi testen.** |
| 11 | Back-up en herstel | Getest via website (met automatische serverherstart en veiligheidsback-up) en CLI. |
| 12 | Langdurig draaien zonder oplopend geheugen | Duurtest met echte mpv: zie onder. **Laat de Pi een paar dagen draaien en kijk op het dashboard.** |
| 13 | Geen afhankelijkheid van PiSignage-cloud | Er is geen PiSignage-code gebruikt; geen uitgaande verbindingen. |
| 14 | Bestaande installatie herstelbaar | Nieuwe SD-kaart; de PiSignage-kaart wordt niet gewijzigd. |

**Duurtest** (geïnstalleerd systeem, echte mpv met `vo=null`, items van 1 seconde, dashboard
dat elke halve seconde de status opvraagt, 7 minuten, ruim 700 statusverzoeken): geheugen van
server 52→53 MB, player-daemon 23 MB, mpv 106→110 MB en daarna vlak; 0 mpv-herstarts,
0 fouten. Dit is een korte duurtest; een test van meerdere dagen op de Pi zelf is aan te raden.

### Controle op de Pi (± 20 minuten)

1. Na installatie en `sudo reboot`: verschijnt het standby-scherm met setup-code?
2. Beheerder aanmaken vanaf je telefoon via `http://signage.local` én via het IP-adres.
3. Upload een foto en een korte MP4 (H.264), maak een playlist, *Opslaan en activeren*.
   Komen beide op het scherm, ook de video **twee keer achter elkaar** (herhaling)?
4. Haal de netwerkkabel van de router naar internet los (de Pi blijft op het lokale netwerk):
   werkt de website nog, en kun je een playlist wijzigen?
5. Trek de stekker uit de Pi en stop hem terug: komt de playlist vanzelf terug?
6. `journalctl -u signage-player -n 50`: staat er `h264_v4l2m2m` zonder foutmeldingen?

---

## 17. Bekende beperkingen

- **Niet getest op echte Pi-hardware.** Weergave via DRM/KMS, hardwaredecodering
  (`v4l2m2m`) op de Pi 3B+, HDMI-geluid, het effect van `gpu_mem=128`, de
  hardware-watchdog en een echte reboot moeten op de Pi worden gecontroleerd (zie §16).
- **De bestaande PiSignage is niet onderzocht**: de toegang tot de Pi is niet gebruikt
  en er is gekozen voor een nieuwe SD-kaart. Er is daarom niets gemigreerd en de
  PiSignage-player is niet hergebruikt; media moeten opnieuw worden geüpload.
- **Overgangen**: tussen items kan heel kort een zwart beeld te zien zijn als mpv het
  volgende bestand opent. Er zijn geen overgangseffecten (fade).
- Eén beheeraccount (er is geen gebruikersbeheer met rollen).
- Geen tijdschema's (playlists op dag/tijd); afspelen hangt bewust niet van de klok af.
- HTTP zonder TLS: bedoeld voor een vertrouwd lokaal netwerk.

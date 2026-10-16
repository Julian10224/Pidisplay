# Controle van de code tegen beide opdrachten

Versie 1.4.0. Per eis: waar het in de code zit en hoe het is gecontroleerd.

**Legenda**

- ✅ voldaan en getest in de testomgeving (Linux x86_64, echte mpv en Chromium, geïnstalleerd met `install.sh`)
- 🟡 voldaan in de code, maar het deel dat hardware of het scherm nodig heeft is **niet op de Raspberry Pi getest**
- ⚠️ gedeeltelijk, met een bewuste en gedocumenteerde beperking
- ❌ niet uitgevoerd

Er was geen toegang tot de Raspberry Pi of het aangesloten scherm. Alles wat ‘op de Pi controleren’ vraagt
staat als 🟡 en is opgenomen in de checklist in README §17.

---

## Opdracht 1 — lokaal digital-signage-systeem

| § | Eis | Status | Waar / hoe gecontroleerd |
|---|---|---|---|
| 1 | Webserver met moderne beheerinterface | ✅ | `routes_*.py`, `static/app.js`; browsertest 22 stappen + 14 stappen |
| 1 | Backend voor media, playlists, schermen, instellingen | ✅ | `routes_media/playlists/players/system.py`; `test_api.py` |
| 1 | Player op het HDMI-scherm | 🟡 | `player/` (browser via cage, mpv als terugval); keten getest met headless cage, niet op HDMI |
| 1 | Lokale database | ✅ | SQLite (`db.py`, WAL) |
| 1 | Lokale mediaopslag | ✅ | `/var/lib/signage/media/…`, veilige namen (UUID) |
| 1 | Herstart na stroomonderbreking | 🟡 | systemd-units + cache van laatste playlist; harde kill getest, echte reboot niet |
| 1 | Volledig offline | 🟡 | geen uitgaande verbindingen gemeten; echte offline test op de Pi nog doen |
| 1 | Bestaande PiSignage behouden | ✅ | nieuwe SD-kaart; oude kaart wordt niet aangeraakt; installer wijzigt PiSignage nooit |
| 2 | Inventarisatie van de bestaande Pi | ❌ | toegang tot de Pi is geweigerd; daarna is gekozen voor een nieuwe SD-kaart |
| 2 | Back-upplan vóór wijzigingen | ✅ | oude SD-kaart = volledige back-up; installer maakt back-up van `config.txt` |
| 2 | Kort technisch ontwerp | ✅ | README §6 (architectuur, keuzes, risico's) |
| 3 | Lichtgewicht architectuur, geen onnodige containers | ✅ | Debian-pakketten, geen Docker, geen buildstap |
| 3 | Monitoring server/player/opslag/afspelen | ✅ | `/api/status`, dashboard, heartbeat |
| 4 | Dashboard: server, player, playlist, media, opslag, uptime, fouten, instellingen; automatisch verversen | ✅ | `Dashboard` in `app.js`; browsertest |
| 4 | Media: formaten, meerdere uploads, voortgang, naam/grootte/formaat/duur, thumbnails, zoeken/sorteren/verwijderen, validatie, foutmeldingen | ✅ | `routes_media.py`, `media.py`; `test_api.py`, browsertest |
| 4 | Veilige bestandsnamen, geen uploads buiten de map, controle op type/grootte/ruimte | ✅ | tests op padmanipulatie, misleidende extensie, afgebroken upload |
| 4 | Waarschuwing bij verwijderen van gebruikte media | ✅ | 409 + dialoog; ook voor overgangsvideo's |
| 4 | Playlists maken/hernoemen/verwijderen, slepen, duur, herhaling, opslaan+activeren, voorbeeld, actieve playlist zichtbaar | ✅ | `PlaylistEditor`; browsertest (slepen en toetsenbord) |
| 4 | Ongeldige playlist niet activeerbaar; player krijgt wijzigingen zonder herstart | ✅ | `playlist_problems`, ETag-sync; tests |
| 4 | Server, player en scherm gescheiden; status/contact/playlist per player; uitbreidbaar | ✅ | `players`-tabel, externe player met token; download getest, tweede Pi niet |
| 4 | Instellingen: naam, standaardplaylist, standaardduur, player-/kioskmodus, autostart, netwerk, tijdzone/tijd, logboeken, back-up, versie | ✅ | `SettingsPage`; kioskmodus (browser/mpv) sinds 1.1.1 ook in de website |
| 4 | Instellingen echt toegepast | ✅ | via `signage-priv`; getest op het geïnstalleerde systeem (tijdzone, player, weergave) |
| 4 | Geen netwerk-/SSH-wijzigingen die toegang kunnen kosten | ✅ | alleen hostnaam; poort bewust alleen via `signage.ini` |
| 5 | PiSignage-player onderzocht op hergebruik | ❌ | niet onderzocht (geen toegang); eigen player gebouwd |
| 5 | Autostart, full-screen, geen cursor/bediening | 🟡 | `cursor:none`, kiosk, mpv `--cursor-autohide`; niet op het scherm bekeken |
| 5 | Foto's met instelbare duur, video's tot het einde, herhalen | ✅ | beide weergaven getest |
| 5 | Lege playlist / ontbrekende bestanden, laatste geldige playlist, herstel bij vastlopen | ✅ | tests (standby, kapot bestand, vastgelopen mpv, cache) |
| 5 | Geen zwarte schermen / flikkering | ✅ / ⚠️ | browser: gemeten 0 lege frames; mpv-terugval kan kort zwart tonen |
| 5 | Juiste GPU-/hardwareacceleratie-instellingen voor dit model | 🟡 | mpv `v4l2m2m-copy`, `gpu_mem=128`; Chromium-decodering op de Pi onbekend, dashboard meet overgeslagen frames |
| 5 | Formaten documenteren | ✅ | README §5 |
| 6 | Geen cloud/CDN/externe fonts/externe API's | ✅ | alles lokaal meegeleverd; geen externe aanvragen in browsertests |
| 6 | Tijd/NTP/DNS blokkeren niets | ✅ | afspelen onafhankelijk van klok; Chromium zonder achtergrondverkeer |
| 6 | Echte offline test | 🟡 | op de Pi nog doen (checklist) |
| 7 | Bereikbaar op het lokale netwerk, hostnaam instelbaar, IP zichtbaar | 🟡 | getest via loopback en privé-adressen; mDNS op de Pi niet |
| 7 | Niet publiek, beperkte API, wachtwoordhashing, sessies, gevaarlijke uploads geweigerd, geen shell via de website | ✅ | tests op publieke afzender, CSRF, hashing, throttling |
| 7 | Toegang herstellen documenteren | ✅ | README §10; herstel via SD-kaart getest |
| 8 | Autostart, herstartbeleid, logging, watchdog, corruptiebescherming, veilige wissels, back-up, updateprocedure, opslagcontrole | ✅ | systemd, WAL, atomaire schrijfacties, `signage-admin` |
| 8 | Geen agressieve herstartlus; SD-kaart sparen | ✅ | afkoelperiodes, StartLimit, begrensd logboek |
| 9 | Projectmap, broncode, configuratie, idempotente installer, units, back-upscript, herstel- en updateprocedure, README | ✅ | installer meerdere keren en als update getest |
| 10 | 14 acceptatietests | zie onder | |
| 12 | Geen placeholders of niet-werkende knoppen; fouten niet verbergen | ✅ | geen TODO's; beperkingen in README §18 |

**Acceptatietests opdracht 1**

| # | Test | Status |
|---|---|---|
| 1 | Website vanaf ander apparaat | 🟡 |
| 2 | Werkt zonder internet | 🟡 |
| 3 | Media uploaden, bekijken, beheren | ✅ |
| 4 | Playlist maken, aanpassen, opslaan, activeren | ✅ |
| 5 | Player speelt foto's en video's | ✅ logica (echte mpv, Chromium) · 🟡 beeld op de Pi |
| 6 | Hervat na herstart | ✅ na harde kill · 🟡 echte stroomonderbreking |
| 7 | Server en player starten na booten | 🟡 |
| 8 | Kapot bestand → geen permanente crash | ✅ |
| 9 | Mislukte upload → geen half bestand | ✅ |
| 10 | Configuratie blijft na reboot | 🟡 (alles persistent opgeslagen; reboot niet getest) |
| 11 | Werkende back-up en herstel | ✅ |
| 12 | Langdurig stabiel geheugen | ✅ mpv 7 min · 🟡 Chromium op de Pi |
| 13 | Geen afhankelijkheid van PiSignage-cloud | ✅ |
| 14 | Oude installatie herstelbaar | ✅ (oude SD-kaart) |

---

## Opdracht 2 — overgangen, HTML en beeldvullende weergave

| § | Eis | Status | Waar / hoe gecontroleerd |
|---|---|---|---|
| — | Eerst back-up van te wijzigen bestanden | ✅ | 1.0.0 volledig bewaard (eerste commit + zip) |
| — | Bestaande architectuur uitbreiden, geen tweede systeem | ✅ | zelfde server/database/player, uitgebreid |
| 1 | Overgangen aan/uit, video uit de bibliotheek, standaard per playlist, per item anders, voorbeeld, standaard voor nieuwe items | ✅ | `PlaylistEditor`, `routes_playlists.py`; browsertest |
| 1 | Volgorde item → overgang (volledig) → volgend item; niet tegelijk zichtbaar | ✅ | gemeten: overgang ≥ 1,0 s van 1,2 s, steeds bovenste laag |
| 1 | Overgang vooraf laden | ✅ / ⚠️ | browser: vaste, voorgeladen laag; mpv-terugval laadt niet vooraf |
| 1 | Geen zwart tussen item, overgang en volgend item | ✅ / ⚠️ | browser 0 lege frames; mpv kort mogelijk |
| 1 | Niet twee keer achter elkaar, niet uit zichzelf | ✅ | overgang alleen vanuit PLAYING; getest |
| 1 | Ontbrekende of kapotte overgang overslaan | ✅ | beide weergaven getest |
| 1 | Laatste item → eerste item | ✅ | ‘Overgang terug naar item 1’; getest over twee rondes |
| 1 | Toestandsmachine (afspelen, overgang voorbereiden/afspelen, volgend laden/afspelen, fout) | ✅ | `player.js` en `mpv_renderer.py`: LOADING_NEXT, STARTING_NEXT, PLAYING, PREPARE_TRANSITION, PLAYING_TRANSITION, HOLD, STANDBY, ERROR |
| 1 | Geen raceconditions bij wijzigen tijdens afspelen | ✅ | generatieteller + ‘pending’; getest tijdens een lopende overgang |
| 2 | Foto's JPEG/PNG/WebP met duur per foto | ✅ | |
| 2 | Video's: autostart, volledig, door naar volgend, geen bediening, beeldvullend, fouten afvangen | ✅ | inclusief heel korte video's (1.1.1) |
| 2 | Hardwareacceleratie op deze Pi controleren | 🟡 | niet mogelijk zonder Pi; dashboard toont decoder (mpv) of overgeslagen frames (browser) |
| 2 | HTML-bestand of ZIP uploaden; assets behouden | ✅ | `htmlpkg.py`; tests |
| 2 | ZIP veilig: path traversal, bestandstypen, niets buiten de projectmap, vaste structuur, duidelijk startbestand | ✅ | tests op `..`, absolute paden, symlinks, verboden types, ZIP-bom, ontbrekende index |
| 2 | Lokale pagina's via een whitelist; geen willekeurige bestanden of beheerfuncties | ✅ | `BUILTIN_PAGES`; tests |
| 2 | Instelbare duur; pagina kan de playlist niet blokkeren; herstel bij vastlopen | ✅ | vastgelopen pagina getest; aparte processen per site |
| 2 | HTML werkt offline; externe bronnen gedocumenteerd en niet blokkerend | ✅ | waarschuwing bij upload; offline-test met externe verwijzingen |
| 3 | Foto's, video's en HTML vullen het scherm; geen zwarte randen; werkelijke schermresolutie | ✅ / 🟡 | pixelmetingen in Chromium en echte mpv; niet op het fysieke scherm bekeken |
| 3 | Cover-gedrag (bijsnijden, opschalen, niet vervormen); staand en liggend | ✅ | gemeten met staand beeld/video en kleine foto |
| 3 | HTML 100vw/100vh zonder marges; afwijkend ontwerp geschaald | ✅ | ingebouwde pagina's; ‘ontwerpformaat’ schaalt volgens de modus |
| 3 | Viewport = schermresolutie, zoom/DPI | ✅ / 🟡 | `--force-device-scale-factor=1`; viewport wordt gemeld in het dashboard |
| 3 | Schaalmodus Vullen/Passend/Uitrekken, standaard Vullen, per item, uitleg | ✅ | Instellingen → Weergave; per item in de editor |
| 4 | Alle 9 combinaties foto/video/HTML, met en zonder overgang | ✅ | `test_all_switches_*`, gemeten 0 lege frames (browser) |
| 4 | Volgend item vooraf laden | ✅ | HTML pas kort voor het einde (1.1.1) zodat de pagina niet al loopt |
| 5 | Editor: type herkenbaar, naam, type, duur, schaalmodus, overganginstellingen; overgang los van media | ✅ | |
| 6 | Datamodel uitbreiden, veilige migratie, oude playlists blijven werken | ✅ | schema 1 → 2 met back-up; getest op een echte 1.0-database en een geïnstalleerd systeem |
| 7 | 15 tests | zie onder | |
| 8 | Inventarisatie bestaande code en overzicht van wijzigingen | ✅ | CHANGELOG.md |

**Acceptatietests opdracht 2**

| # | Test | Status |
|---|---|---|
| 1–3 | Foto's, video's, HTML beeldvullend | ✅ gemeten · 🟡 fysiek scherm |
| 4 | Standaard geen zwarte randen | ✅ |
| 5 | Verhouding behouden bij Vullen | ✅ |
| 6 | Passend | ✅ |
| 7 | Uitrekken | ✅ |
| 8 | Overgang volledig tussen twee items | ✅ |
| 9 | Zonder overgangen normaal | ✅ |
| 10 | Ontbrekende/kapotte overgang blokkeert niet | ✅ |
| 11 | HTML zonder internet | ✅ |
| 12 | HTML gaat door na de ingestelde tijd | ✅ |
| 13 | Mix van foto's, video's en HTML | ✅ |
| 14 | Configuratie blijft na reboot | 🟡 |
| 15 | Stabiel bij langdurig afspelen | ✅ mpv · 🟡 Chromium op de Pi (lek bij overgangen gevonden en opgelost in 1.1.1) |

---

## Aanvullende wensen (1.2.0)

| Wens | Status | Waar / hoe gecontroleerd |
|---|---|---|
| Schakelaar voor willekeurige volgorde per playlist | ✅ | editor + `playlists.shuffle`; browser- en mpv-tests: elk item één keer per ronde, geschud, nooit twee keer achter elkaar; geïnstalleerd systeem 10/10 |
| Per item een PNG-logo linksboven | ✅ / 🟡 | `playlist_items.logo_media_id`; pixelmetingen in Chromium (positie, 18%, transparantie, niet tijdens overgang) en met de echte mpv (ook 90° gedraaid); niet op het fysieke scherm bekeken |
| Pagina met alles wat op de Pi geüpload is, met verwijderen | ✅ | *Media* uitgebreid tot overzicht (opslag, gebruik, filters, meerdere tegelijk verwijderen); API- en browsertests |

---

## Aanvullende wensen (1.3.x)

| Wens | Status | Waar / hoe gecontroleerd |
|---|---|---|
| Knop in de website om de live playlist door te klikken en alles in een paar seconden te testen | ✅ / 🟡 | *Vorige*/*Volgende* op dashboard en *Schermen*, klikbare filmstrip, ← →, optioneel met overgang; `test_v13.py`, 8 browsertests (ook de hele keten met de browserweergave) (0 frames zwart bij doorklikken), geïnstalleerd systeem met echte mpv 10/10 (0,2–0,4 s); niet op het fysieke scherm bekeken |
| Willekeurige volgorde blijft willekeurig bij de live bediening | ✅ | niet te reproduceren als volgorde van de playlist (gemeten in beide weergaven en via de server); wel twee verwarrende punten opgelost: dashboardteller en onvolledige eerste ronde na aanzetten tijdens afspelen; tests in `test_v13.py` en browsertest |
| Software bijwerken met een knop in de website | ✅ | `routes_update.py`, `bin/signage-update`, `signage-priv update`; getest op het geïnstalleerde systeem: knop → download van GitHub → installatie → herstart → pagina laadt 1.3.2 (± 18 s, zonder nieuwe pakketten) |
| Logo in het midden met laadbalk bij het opstarten | ✅ / 🟡 | Plymouth-thema via `signage-priv splash`, eigen logo onder *Instellingen → Opstartscherm*; gerenderd met Plymouth op een virtueel scherm (0°/90°); `test_splash.py`; **niet op de Pi bekeken** |
| Oorzaak zien als de browserweergave niet start | 🟡 | launcher bewaart uitvoer + diagnose, zichtbaar in *Instellingen → Weergave*; getest met een nagebootste cage. **De echte oorzaak op de Pi is nog onbekend** |

---

## Wat nog openstaat

1. **Testen op de Raspberry Pi zelf** (README §17): beeld op het scherm, Chromium op 1 GB RAM en
   de videodecodering daarin, cage met een sessie op tty1, reboot, echte offline test, meerdaagse duurtest.
2. **Inventarisatie en migratie van de bestaande PiSignage** (opdracht 1 §2 en fase 5) zijn niet
   uitgevoerd, omdat er geen toegang tot de Pi was en daarna voor een nieuwe SD-kaart is gekozen.
   De media moeten opnieuw worden geüpload.
3. **Een direct flashbaar image** is niet geleverd: het basisimage van Raspberry Pi OS kon niet worden
   gedownload. In plaats daarvan: Raspberry Pi OS Lite flashen + één installatieopdracht.

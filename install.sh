#!/bin/bash
# =============================================================================
#  Signage — installatie- en updatescript voor Raspberry Pi OS (Lite)
#
#  Gebruik (vanaf de boot-partitie van de SD-kaart):
#     sudo bash /boot/firmware/signage/install.sh
#  Of via SSH, zonder iets op de SD-kaart te zetten (downloadt van GitHub, zie get.sh):
#     curl -fsSL https://raw.githubusercontent.com/Julian10224/Pidisplay/main/get.sh | sudo bash
#
#  Opties:
#     --role all|server|player   Wat installeren (standaard: all = server + player)
#     --server URL               (alleen --role player) adres van de signage-server
#     --token TOKEN              (alleen --role player) player-token uit de beheerwebsite
#     --port N                   Poort van de beheerwebsite bij eerste installatie (standaard 80)
#     --offline                  Geen pakketten installeren; stoppen als er iets ontbreekt
#     --disable-desktop          Desktop uitzetten als die het scherm bezet houdt
#     --no-watchdog              Hardware-watchdog niet inschakelen
#     --no-gpu-mem               config.txt niet aanpassen (gpu_mem voor videodecodering)
#     --no-browser               Geen Chromium/cage installeren; alleen mpv-weergave (geen HTML)
#     --no-splash                Geen opstartscherm (logo met laadbalk via Plymouth); laat
#                                cmdline.txt/config.txt ongemoeid en draait een eerder
#                                ingesteld opstartscherm terug (--splash zet het weer aan)
#     --help
#
#  Het script is veilig opnieuw uit te voeren (ook voor updates): bestaande
#  configuratie, database en media blijven altijd behouden. Opties die je bij een
#  update niet opgeeft (--role, --no-browser, --no-watchdog, --no-gpu-mem, --no-splash) komen uit
#  /etc/signage/install.conf, dat bij de eerste installatie wordt aangemaakt.
# =============================================================================
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIG_VERSION="$(tr -d '[:space:]' < "$SRC/VERSION")"
ROLE=""
SERVER_URL=""
TOKEN=""
PORT=80
OFFLINE=0
DISABLE_DESKTOP=0
WATCHDOG=""
GPU_MEM=""
BROWSER=""
SPLASH=""
INSTALL_CONF=/etc/signage/install.conf
LOG=/var/log/signage-install.log
NEEDS_REBOOT=0

c_ok() { printf '\033[32m✔\033[0m %s\n' "$*"; }
c_info() { printf '\033[36m•\033[0m %s\n' "$*"; }
c_warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
die() { printf '\033[31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --role) ROLE="${2:-}"; shift 2 ;;
        --server) SERVER_URL="${2:-}"; shift 2 ;;
        --token) TOKEN="${2:-}"; shift 2 ;;
        --port) PORT="${2:-}"; shift 2 ;;
        --offline) OFFLINE=1; shift ;;
        --disable-desktop) DISABLE_DESKTOP=1; shift ;;
        --no-watchdog) WATCHDOG=0; shift ;;
        --no-gpu-mem) GPU_MEM=0; shift ;;
        --no-browser) BROWSER=0; shift ;;
        --no-splash) SPLASH=0; shift ;;
        --splash) SPLASH=1; shift ;;
        -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
        *) die "Onbekende optie: $1 (zie --help)" ;;
    esac
done

# Niet opgegeven opties: zoals bij de vorige installatie (update), anders de standaard.
saved() { if [ -f "$INSTALL_CONF" ]; then sed -n "s/^$1=\([a-z0-9]*\)$/\1/p" "$INSTALL_CONF" | tail -n 1; fi; }
EXISTING=0
{ [ -f /etc/signage/signage.ini ] || [ -f /etc/signage/player.ini ]; } && EXISTING=1
[ -n "$ROLE" ] || ROLE="$(saved role)"
if [ -z "$ROLE" ]; then
    ROLE=all
    # Installatie van vóór 1.3.2 (zonder install.conf): rol afleiden uit de services.
    if [ $EXISTING = 1 ]; then
        srv=0; ply=0
        systemctl is-enabled --quiet signage-server.service 2>/dev/null && srv=1
        systemctl is-enabled --quiet signage-player.service 2>/dev/null && ply=1
        if [ $srv = 1 ] && [ $ply = 0 ]; then ROLE=server; fi
        if [ $srv = 0 ] && [ $ply = 1 ]; then ROLE=player; fi
    fi
fi
[ -n "$BROWSER" ] || BROWSER="$(saved browser)"
if [ -z "$BROWSER" ]; then
    BROWSER=1
    # Bestaande installatie zonder cage: toen met --no-browser geïnstalleerd; zo laten.
    [ $EXISTING = 1 ] && [ -f /etc/signage/player.ini ] && ! command -v cage >/dev/null 2>&1 && BROWSER=0
fi
[ -n "$SPLASH" ] || SPLASH="$(saved splash)"
[ -n "$SPLASH" ] || SPLASH=1     # nieuw in 1.4: ook bij een update standaard aan
[ -n "$WATCHDOG" ] || WATCHDOG="$(saved watchdog)"
[ -n "$GPU_MEM" ] || GPU_MEM="$(saved gpu_mem)"
# Bij een update zonder install.conf geen nieuwe systeemwijzigingen (watchdog, config.txt).
[ -n "$WATCHDOG" ] || { [ $EXISTING = 1 ] && WATCHDOG=0 || WATCHDOG=1; }
[ -n "$GPU_MEM" ] || { [ $EXISTING = 1 ] && GPU_MEM=0 || GPU_MEM=1; }

case "$ROLE" in all|server|player) ;; *) die "--role moet all, server of player zijn." ;; esac
case "$BROWSER$WATCHDOG$GPU_MEM$SPLASH" in [01][01][01][01]) ;; *) die "Ongeldige waarden in $INSTALL_CONF." ;; esac
[[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "Ongeldige poort: $PORT"
if [ "$ROLE" = player ]; then
    [ -n "$SERVER_URL" ] || [ -f /etc/signage/player.ini ] || die "--role player vereist --server URL en --token TOKEN."
    [ -n "$TOKEN" ] || [ -f /etc/signage/player.ini ] || die "--role player vereist --token TOKEN."
fi
[ "$(id -u)" = 0 ] || die "Voer dit script uit met sudo: sudo bash $0"

has_server() { [ "$ROLE" = all ] || [ "$ROLE" = server ]; }
has_player() { [ "$ROLE" = all ] || [ "$ROLE" = player ]; }

mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
echo
echo "=== Signage $SIG_VERSION installatie ($ROLE) — $(date '+%Y-%m-%d %H:%M:%S') ==="

# -----------------------------------------------------------------------------
# 1. Controle van het systeem
# -----------------------------------------------------------------------------
MODEL="$( { tr -d '\0' < /proc/device-tree/model; } 2>/dev/null || echo onbekend)"
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
# os-release in een subshell lezen: het definieert zelf een variabele VERSION.
OS_PRETTY="$( . /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-onbekend}")"
OS_VERSION_ID="$( . /etc/os-release 2>/dev/null; echo "${VERSION_ID:-}")"
c_info "Apparaat: $MODEL"
c_info "Systeem:  $OS_PRETTY ($ARCH), kernel $(uname -r)"

command -v apt-get >/dev/null || die "Dit script vereist Raspberry Pi OS / Debian (apt)."
case "$ARCH" in
    armhf|arm64|aarch64) ;;
    *) c_warn "Architectuur $ARCH is niet getest; installatie gaat door." ;;
esac
case "$MODEL" in
    *"Raspberry Pi 3 Model B Plus"*) ;;
    *"Raspberry Pi"*) c_warn "Ontworpen en afgestemd op de Raspberry Pi 3B+; dit model wordt niet specifiek ondersteund." ;;
    *) c_warn "Geen Raspberry Pi gedetecteerd." ;;
esac
case "$OS_VERSION_ID" in
    12|13) ;;
    *) c_warn "Debian-versie ${OS_VERSION_ID:-?} is niet getest (verwacht: 12 Bookworm of 13 Trixie)." ;;
esac

ROOT_FREE_MB=$(df -Pm / | awk 'NR==2 {print $4}')
[ "$ROOT_FREE_MB" -ge 700 ] || die "Te weinig vrije ruimte op de SD-kaart (${ROOT_FREE_MB} MB, minimaal 700 MB nodig)."
c_ok "Vrije ruimte: ${ROOT_FREE_MB} MB"

BOOT_DIR=/boot/firmware
[ -d "$BOOT_DIR" ] || BOOT_DIR=/boot

# Bestaande PiSignage: nooit aanraken, wel melden.
if [ -d /home/pi/piSignagePro ] || systemctl list-unit-files 2>/dev/null | grep -qi pisignage; then
    c_warn "Er is een PiSignage-installatie gevonden. Deze wordt NIET gewijzigd of verwijderd."
    c_warn "Let op: twee players tegelijk op één scherm werkt niet. Stop PiSignage zelf als je wilt overstappen."
fi

# Poort vrij?
if has_server && command -v ss >/dev/null; then
    if ss -Hltn "sport = :$PORT" 2>/dev/null | grep -q . && ! systemctl is-active --quiet signage-server.service; then
        if [ ! -f /etc/signage/signage.ini ]; then
            die "Poort $PORT is al in gebruik door een ander programma. Kies een andere met --port."
        fi
    fi
fi

# Desktop bezet het scherm => mpv kan niet tekenen.
if has_player; then
    DM_ACTIVE=0
    if systemctl is-active --quiet display-manager.service 2>/dev/null; then DM_ACTIVE=1; fi
    if [ "$(systemctl get-default 2>/dev/null)" = graphical.target ] || [ $DM_ACTIVE = 1 ]; then
        if [ $DISABLE_DESKTOP = 1 ]; then
            systemctl set-default multi-user.target
            c_ok "Desktop uitgeschakeld bij het opstarten (terugzetten: sudo systemctl set-default graphical.target)."
            NEEDS_REBOOT=1
        else
            die "Er draait een desktop die het scherm bezet houdt. Gebruik Raspberry Pi OS Lite, of voer dit script uit met --disable-desktop."
        fi
    fi
fi

# -----------------------------------------------------------------------------
# 2. Pakketten
# -----------------------------------------------------------------------------
PKGS=(python3 sudo iproute2)
if has_server; then PKGS+=(python3-flask python3-waitress python3-pil ffmpeg fonts-dejavu-core avahi-daemon); fi
if has_player; then PKGS+=(mpv fonts-dejavu-core python3-pil); fi
if has_player && [ "$SPLASH" = 1 ]; then PKGS+=(plymouth); fi

# Browserweergave (HTML-content, overgangen met voorgeladen lagen): cage + Chromium.
BROWSER_PKGS=()
if has_player && [ $BROWSER = 1 ]; then
    CHROMIUM_PKG=chromium
    # Raspberry Pi OS Bookworm levert de voor de Pi aangepaste versie als 'chromium-browser'.
    if apt-cache policy chromium-browser 2>/dev/null | grep -q 'Candidate: [0-9]'; then
        CHROMIUM_PKG=chromium-browser
    fi
    if apt-cache policy "$CHROMIUM_PKG" 2>/dev/null | grep -q 'Candidate:.*snap'; then
        c_warn "Chromium is op dit systeem alleen als snap beschikbaar; browserweergave wordt overgeslagen (mpv-weergave)."
    else
        BROWSER_PKGS=(cage "$CHROMIUM_PKG")
    fi
fi

MISSING=()
for p in "${PKGS[@]}"; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || MISSING+=("$p")
done
if [ ${#MISSING[@]} -gt 0 ]; then
    if [ $OFFLINE = 1 ]; then
        die "Ontbrekende pakketten en --offline opgegeven: ${MISSING[*]}"
    fi
    c_info "Pakketten installeren (internet nodig): ${MISSING[*]}"
    if ! apt-get update; then
        die "apt-get update mislukt. Is er een internetverbinding? (Alleen de installatie heeft internet nodig.)"
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${MISSING[@]}" \
        || die "Installeren van pakketten mislukt; zie $LOG."
fi
c_ok "Benodigde pakketten aanwezig"

if [ ${#BROWSER_PKGS[@]} -gt 0 ]; then
    BMISSING=()
    for p in "${BROWSER_PKGS[@]}"; do
        dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || BMISSING+=("$p")
    done
    if [ ${#BMISSING[@]} -gt 0 ] && [ $OFFLINE = 0 ]; then
        c_info "Browserweergave installeren (kan op een Pi 3B+ enkele minuten duren): ${BMISSING[*]}"
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${BMISSING[@]}" || true
        BMISSING=()
        for p in "${BROWSER_PKGS[@]}"; do
            dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || BMISSING+=("$p")
        done
    fi
    if [ ${#BMISSING[@]} -eq 0 ]; then
        c_ok "Browserweergave beschikbaar (cage + Chromium)"
    else
        c_warn "Browserweergave niet beschikbaar (ontbreekt: ${BMISSING[*]}); de player gebruikt mpv (geen HTML)."
    fi
fi

# Snelle controle dat de Python-modules werken.
if has_server; then
    python3 -c "import flask, waitress, PIL" 2>/dev/null || die "Python-modules flask/waitress/PIL werken niet."
fi

# -----------------------------------------------------------------------------
# 3. Gebruiker en mappen
# -----------------------------------------------------------------------------
if ! id signage >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/signage --no-create-home --shell /usr/sbin/nologin signage
    c_ok "Systeemgebruiker 'signage' aangemaakt"
fi
for g in video render audio input systemd-journal; do
    if getent group "$g" >/dev/null; then usermod -aG "$g" signage; fi
done

install -d -o root -g signage -m 0750 /etc/signage
install -d -o signage -g signage -m 0750 /var/lib/signage
install -d -o signage -g signage -m 0750 /var/lib/signage/player

# -----------------------------------------------------------------------------
# 4. Software (releases met rollback)
# -----------------------------------------------------------------------------
REL=/opt/signage/releases/$SIG_VERSION
install -d -o root -g root -m 0755 /opt/signage /opt/signage/releases
TMP_REL="$(mktemp -d /opt/signage/releases/.tmp-XXXXXX)"
cp -r "$SRC/app" "$SRC/bin" "$SRC/VERSION" "$TMP_REL/"
[ -f "$SRC/README.md" ] && cp "$SRC/README.md" "$TMP_REL/"
find "$TMP_REL" -name "__pycache__" -prune -exec rm -rf {} +
chown -R root:root "$TMP_REL"
find "$TMP_REL" -type d -exec chmod 0755 {} +
find "$TMP_REL" -type f -exec chmod 0644 {} +
chmod 0755 "$TMP_REL" "$TMP_REL"/bin/*

CUR="$(readlink -f /opt/signage/current 2>/dev/null || true)"
if [ -d "$REL" ] && diff -rq "$REL" "$TMP_REL" >/dev/null 2>&1; then
    rm -rf "$TMP_REL"
    c_ok "Software $SIG_VERSION is al geïnstalleerd en ongewijzigd"
else
    if [ -d "$REL" ]; then
        rm -rf "$REL.old"
        mv "$REL" "$REL.old"
    fi
    mv "$TMP_REL" "$REL"
    rm -rf "$REL.old"
    c_ok "Software $SIG_VERSION geïnstalleerd in $REL"
fi
if [ -n "$CUR" ] && [ "$CUR" != "$REL" ] && [ -d "$CUR" ]; then
    ln -sfn "$CUR" /opt/signage/previous.tmp && mv -T /opt/signage/previous.tmp /opt/signage/previous
    c_info "Vorige versie bewaard voor rollback: $(basename "$CUR")"
fi
ln -sfn "$REL" /opt/signage/current.tmp && mv -T /opt/signage/current.tmp /opt/signage/current

# Oude releases opruimen (huidige en vorige blijven).
KEEP_PREV="$(readlink -f /opt/signage/previous 2>/dev/null || true)"
for d in /opt/signage/releases/*/; do
    d="${d%/}"
    [ "$d" = "$REL" ] || [ "$d" = "$KEEP_PREV" ] || rm -rf "$d"
done

install -o root -g root -m 0755 "$SRC/bin/signage-priv" /usr/local/sbin/signage-priv
install -o root -g root -m 0755 "$SRC/bin/signage-admin" /usr/local/bin/signage-admin

if has_server; then
    TMP_SUDO="$(mktemp)"
    cp "$SRC/config/sudoers-signage" "$TMP_SUDO"
    if visudo -cf "$TMP_SUDO" >/dev/null; then
        install -o root -g root -m 0440 "$TMP_SUDO" /etc/sudoers.d/signage
    else
        rm -f "$TMP_SUDO"
        die "sudoers-regel ongeldig; niet geïnstalleerd."
    fi
    rm -f "$TMP_SUDO"
fi

# -----------------------------------------------------------------------------
# 5. Configuratie (nooit overschrijven)
# -----------------------------------------------------------------------------
set_ini() {  # set_ini bestand sleutel waarde  (alleen bestaande sleutel vervangen)
    local f="$1" k="$2" v="$3"
    python3 - "$f" "$k" "$v" <<'PY'
import re, sys
f, k, v = sys.argv[1:4]
s = open(f, encoding="utf-8").read()
s, n = re.subn(rf"(?m)^{re.escape(k)}\s*=.*$", lambda m: f"{k} = {v}", s, count=1)
if n == 0:
    s = s.rstrip("\n") + f"\n{k} = {v}\n"
open(f, "w", encoding="utf-8").write(s)
PY
}

if has_server && [ ! -f /etc/signage/signage.ini ]; then
    install -o root -g signage -m 0640 "$SRC/config/signage.ini" /etc/signage/signage.ini
    set_ini /etc/signage/signage.ini port "$PORT"
    c_ok "Configuratie aangemaakt: /etc/signage/signage.ini (poort $PORT)"
elif has_server; then
    c_ok "Bestaande configuratie behouden: /etc/signage/signage.ini"
fi
if has_server; then
    PORT="$(python3 -c "import configparser;c=configparser.ConfigParser();c.read('/etc/signage/signage.ini');print(c.get('server','port',fallback='80'))")"
fi

if has_player; then
    if [ ! -f /etc/signage/player.ini ]; then
        install -o root -g signage -m 0640 "$SRC/config/player.ini" /etc/signage/player.ini
        [ $BROWSER = 0 ] && set_ini /etc/signage/player.ini renderer mpv
        if [ "$ROLE" = player ]; then
            set_ini /etc/signage/player.ini server_url "$SERVER_URL"
            set_ini /etc/signage/player.ini token "$TOKEN"
        else
            set_ini /etc/signage/player.ini server_url "http://127.0.0.1:$PORT"
            set_ini /etc/signage/player.ini token "$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
        fi
        c_ok "Player-configuratie aangemaakt: /etc/signage/player.ini"
    else
        [ -n "$SERVER_URL" ] && set_ini /etc/signage/player.ini server_url "$SERVER_URL"
        [ -n "$TOKEN" ] && set_ini /etc/signage/player.ini token "$TOKEN"
        [ $BROWSER = 0 ] && set_ini /etc/signage/player.ini renderer mpv
        c_ok "Bestaande player-configuratie behouden: /etc/signage/player.ini"
    fi
    chown root:signage /etc/signage/player.ini && chmod 0640 /etc/signage/player.ini
elif has_server && [ ! -f /etc/signage/player.ini ]; then
    # Server zonder lokale player: leeg token => lokale player uitgeschakeld.
    install -o root -g signage -m 0640 "$SRC/config/player.ini" /etc/signage/player.ini
fi

if has_player; then
    install -o root -g root -m 0644 "$SRC/config/pam-signage-player" /etc/pam.d/signage-player
fi

# -----------------------------------------------------------------------------
# 6. Systeeminstellingen (beperkt en omkeerbaar)
# -----------------------------------------------------------------------------
# Logboek begrenzen (SD-kaart sparen).
install -d -m 0755 /etc/systemd/journald.conf.d
if [ ! -f /etc/systemd/journald.conf.d/signage.conf ]; then
    printf '# Signage: logboek begrenzen om de SD-kaart te sparen\n[Journal]\nSystemMaxUse=64M\nMaxRetentionSec=1month\n' \
        > /etc/systemd/journald.conf.d/signage.conf
    systemctl restart systemd-journald || true
    c_ok "Logboek begrensd tot 64 MB"
fi

# Hardware-watchdog: herstart de Pi automatisch als het systeem volledig vastloopt.
if [ $WATCHDOG = 1 ] && { [ -e /dev/watchdog ] || [ -e /dev/watchdog0 ]; }; then
    install -d -m 0755 /etc/systemd/system.conf.d
    if [ ! -f /etc/systemd/system.conf.d/signage-watchdog.conf ]; then
        printf '# Signage: hardware-watchdog (verwijder dit bestand om uit te schakelen)\n[Manager]\nRuntimeWatchdogSec=15\n' \
            > /etc/systemd/system.conf.d/signage-watchdog.conf
        c_ok "Hardware-watchdog ingeschakeld (actief na herstart)"
        NEEDS_REBOOT=1
    fi
fi

# GPU-geheugen voor hardwarematige H.264-decodering op de Pi 3.
if has_player && [ $GPU_MEM = 1 ] && [[ "$MODEL" == *"Raspberry Pi 3"* ]]; then
    CFG_TXT="$BOOT_DIR/config.txt"
    if [ -f "$CFG_TXT" ] && ! grep -Eq '^[[:space:]]*gpu_mem' "$CFG_TXT"; then
        cp "$CFG_TXT" "$CFG_TXT.signage-backup-$(date +%Y%m%d%H%M%S)"
        printf '\n# Signage: geheugen voor hardwarematige videodecodering (Pi 3)\n[all]\ngpu_mem=128\n' >> "$CFG_TXT"
        c_ok "gpu_mem=128 toegevoegd aan $CFG_TXT (back-up gemaakt; actief na herstart)"
        NEEDS_REBOOT=1
    fi
fi

# Opstartscherm: logo in het midden met een laadbalk (Plymouth-thema 'signage').
if has_player && [ "$SPLASH" = 1 ]; then
    if command -v plymouthd >/dev/null 2>&1; then
        c_info "Opstartscherm instellen (kan op een Pi 3B+ 1–2 minuten duren)…"
        if out="$(/usr/local/sbin/signage-priv splash-sync 2>&1)"; then
            c_ok "Opstartscherm ingesteld (logo met laadbalk; kies je eigen logo onder Instellingen → Opstartscherm)"
        else
            c_warn "Opstartscherm instellen mislukt: $out"
        fi
        if out="$("$REL/bin/signage-bootconfig" enable "$BOOT_DIR" 2>&1)"; then
            if [ "$out" = changed ]; then
                c_ok "Opstartregels aangepast voor het opstartscherm (quiet splash; back-up van cmdline.txt en config.txt gemaakt)"
                NEEDS_REBOOT=1
            fi
        else
            c_warn "cmdline.txt/config.txt niet aangepast: $out"
        fi
    else
        c_warn "Plymouth ontbreekt; geen opstartscherm."
    fi
elif has_player && [ "$SPLASH" = 0 ] && [ -f /etc/signage/bootconfig-added ]; then
    if out="$("$REL/bin/signage-bootconfig" disable "$BOOT_DIR" 2>&1)" && [ "$out" = changed ]; then
        c_ok "Opstartscherm uitgezet (opstartregels teruggezet)"
        NEEDS_REBOOT=1
    fi
fi

# -----------------------------------------------------------------------------
# 7. systemd
# -----------------------------------------------------------------------------
for u in signage-server.service signage-player.service signage-backup.service signage-backup.timer; do
    install -o root -g root -m 0644 "$SRC/systemd/$u" "/etc/systemd/system/$u"
done
systemctl daemon-reload

if has_server; then
    systemctl enable signage-server.service signage-backup.timer >/dev/null 2>&1
    systemctl restart signage-server.service
    systemctl start signage-backup.timer
else
    systemctl disable --now signage-server.service signage-backup.timer >/dev/null 2>&1 || true
fi
if has_player; then
    systemctl enable signage-player.service >/dev/null 2>&1
    systemctl restart signage-player.service
else
    systemctl disable --now signage-player.service >/dev/null 2>&1 || true
fi
c_ok "Services ingeschakeld en gestart"

# -----------------------------------------------------------------------------
# 8. Controle
# -----------------------------------------------------------------------------
if has_server; then
    OK=0
    for _ in $(seq 1 40); do
        if python3 - "$PORT" <<'PY' 2>/dev/null; then OK=1; break; fi
import sys, urllib.request
urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/auth/state", timeout=2).read()
PY
        sleep 1
    done
    [ $OK = 1 ] || die "De server reageert niet. Bekijk: journalctl -u signage-server -n 50"
    c_ok "Beheerwebsite reageert"
fi
if has_player; then
    sleep 3
    if systemctl is-active --quiet signage-player.service; then
        c_ok "Player draait"
    else
        c_warn "Player draait (nog) niet. Bekijk: journalctl -u signage-player -n 50"
    fi
fi

# Gekozen opties bewaren voor volgende updates (ook via de knop in de beheerwebsite).
printf '# Signage: opties van de installatie, gebruikt bij updates zonder opties.\nrole=%s\nbrowser=%s\nwatchdog=%s\ngpu_mem=%s\nsplash=%s\n' \
    "$ROLE" "$BROWSER" "$WATCHDOG" "$GPU_MEM" "$SPLASH" > "$INSTALL_CONF.tmp" && chmod 0644 "$INSTALL_CONF.tmp" && mv -f "$INSTALL_CONF.tmp" "$INSTALL_CONF"

echo
echo "=== Klaar: Signage $SIG_VERSION ==="
if has_server; then
    PSUF=""; [ "$PORT" = 80 ] || PSUF=":$PORT"
    echo "Beheerwebsite:"
    echo "   http://$(hostname).local$PSUF"
    for ip in $(hostname -I 2>/dev/null); do
        [[ "$ip" == *:* ]] || echo "   http://$ip$PSUF"
    done
    if [ -f /var/lib/signage/setup_code ]; then
        echo "Setup-code (staat ook op het scherm): $(cat /var/lib/signage/setup_code)"
    fi
fi
if [ $NEEDS_REBOOT = 1 ]; then
    c_warn "Herstart de Raspberry Pi om alle instellingen te activeren:  sudo reboot"
fi
echo "Logboek van deze installatie: $LOG"

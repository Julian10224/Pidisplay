#!/bin/bash
# Signage verwijderen.
#   sudo bash uninstall.sh           services, software en systeemwijzigingen verwijderen;
#                                    database, media en back-ups (/var/lib/signage) en
#                                    configuratie (/etc/signage) blijven bewaard
#   sudo bash uninstall.sh --purge   ook gegevens, configuratie en gebruiker verwijderen
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Gebruik: sudo bash $0 [--purge]" >&2; exit 1; }
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

if [ $PURGE = 1 ]; then
    read -r -p "Alle media, playlists en back-ups definitief verwijderen? Typ 'ja': " ans
    [ "$ans" = "ja" ] || { echo "Afgebroken."; exit 1; }
fi

for u in signage-player.service signage-server.service signage-backup.timer signage-backup.service; do
    systemctl disable --now "$u" >/dev/null 2>&1 || true
    rm -f "/etc/systemd/system/$u"
done
systemctl daemon-reload
# Opstartscherm terugdraaien (alleen onze eigen toevoegingen aan cmdline.txt/config.txt).
BOOT_DIR=/boot/firmware; [ -d "$BOOT_DIR" ] || BOOT_DIR=/boot
if [ -x /opt/signage/current/bin/signage-bootconfig ] && [ -f /etc/signage/bootconfig-added ]; then
    /opt/signage/current/bin/signage-bootconfig disable "$BOOT_DIR" >/dev/null && echo "Opstartregels teruggezet."
fi
if [ -d /usr/share/plymouth/themes/signage ]; then
    rm -rf /usr/share/plymouth/themes/signage
    [ -f /etc/plymouth/plymouthd.conf ] && sed -i '/^Theme=signage$/d' /etc/plymouth/plymouthd.conf
    command -v update-initramfs >/dev/null && ls "$BOOT_DIR"/initramfs* >/dev/null 2>&1 && update-initramfs -u >/dev/null 2>&1 || true
    echo "Opstartscherm verwijderd."
fi
rm -f /etc/sudoers.d/signage /usr/local/sbin/signage-priv /usr/local/bin/signage-admin /etc/pam.d/signage-player
rm -f /etc/systemd/journald.conf.d/signage.conf /etc/systemd/system.conf.d/signage-watchdog.conf
rm -rf /opt/signage
echo "Software en services verwijderd."
echo "Let op: een eventuele 'gpu_mem=128'-regel in config.txt en de opstartmodus blijven ongewijzigd."

if [ $PURGE = 1 ]; then
    rm -rf /var/lib/signage /etc/signage
    userdel signage 2>/dev/null || true
    echo "Gegevens, configuratie en gebruiker verwijderd."
else
    echo "Gegevens bewaard in /var/lib/signage en /etc/signage."
fi

#!/bin/bash
# =============================================================================
#  Signage — installeren of updaten via SSH, zonder iets op de SD-kaart te zetten
#
#  Op de Raspberry Pi (ingelogd via SSH, met internet):
#     curl -fsSL https://raw.githubusercontent.com/Julian10224/Pidisplay/main/get.sh | sudo bash
#
#  Met opties voor install.sh (zie install.sh --help), bijvoorbeeld een losse player:
#     curl -fsSL https://raw.githubusercontent.com/Julian10224/Pidisplay/main/get.sh \
#       | sudo bash -s -- --role player --server http://signage.local --token TOKEN
#
#  Eigen opties van dit script:
#     --ref REF    Branch, tag of commit van GitHub (standaard: main)
#
#  Wat het doet: downloadt de broncode als archief van GitHub naar een tijdelijke map,
#  controleert dat het een Signage-pakket is en start daarna install.sh. Configuratie,
#  database en media blijven behouden, net als bij een gewone update. De tijdelijke map
#  wordt na afloop verwijderd.
# =============================================================================
set -euo pipefail
TMP_DIR=""

# Alles staat in main(): bij 'curl | bash' leest bash het script in zijn geheel voordat
# er iets wordt uitgevoerd, zodat een afgebroken download nooit half wordt uitgevoerd.
main() {
    local repo="${SIGNAGE_REPO:-Julian10224/Pidisplay}"
    local ref="${SIGNAGE_REF:-main}"
    local args=()
    while [ $# -gt 0 ]; do
        case "$1" in
            --ref) ref="${2:-}"; shift 2 ;;
            --ref=*) ref="${1#--ref=}"; shift ;;
            *) args+=("$1"); shift ;;
        esac
    done

    if [ "$(id -u)" -ne 0 ]; then
        echo "Start dit script met sudo:  curl -fsSL …/get.sh | sudo bash" >&2
        exit 1
    fi
    case "$ref" in
        ''|*[!A-Za-z0-9._/-]*) echo "Ongeldige --ref: '$ref'" >&2; exit 1 ;;
    esac

    # SIGNAGE_URL is alleen bedoeld voor tests (lokaal archief).
    local url="${SIGNAGE_URL:-https://codeload.github.com/$repo/tar.gz/$ref}"
    TMP_DIR="$(mktemp -d /tmp/signage-get.XXXXXX)"
    trap 'rm -rf "$TMP_DIR"' EXIT
    local tmp="$TMP_DIR"

    echo "Signage downloaden van github.com/$repo ($ref)…"
    if command -v curl >/dev/null; then
        curl -fsSL --retry 3 --connect-timeout 20 "$url" -o "$tmp/src.tar.gz" \
            || { echo "Downloaden mislukt. Heeft de Pi internet? ($url)" >&2; exit 1; }
    elif command -v wget >/dev/null; then
        wget -q -O "$tmp/src.tar.gz" "$url" \
            || { echo "Downloaden mislukt. Heeft de Pi internet? ($url)" >&2; exit 1; }
    else
        echo "curl of wget is nodig om te downloaden." >&2
        exit 1
    fi

    mkdir "$tmp/src"
    tar -xzf "$tmp/src.tar.gz" -C "$tmp/src" --no-same-owner \
        || { echo "Het gedownloade archief is beschadigd." >&2; exit 1; }
    local src
    src="$(find "$tmp/src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
    if [ -z "$src" ] || [ ! -f "$src/install.sh" ] || [ ! -f "$src/VERSION" ] || [ ! -d "$src/app/signage" ]; then
        echo "Het gedownloade archief is geen Signage-pakket." >&2
        exit 1
    fi
    echo "Signage $(tr -d '[:space:]' < "$src/VERSION") gedownload; installatie start."
    echo

    # stdin is bij 'curl | bash' het script zelf; de installatie krijgt daarom /dev/null.
    local rc=0
    bash "$src/install.sh" "${args[@]}" < /dev/null || rc=$?
    return "$rc"
}

main "$@"

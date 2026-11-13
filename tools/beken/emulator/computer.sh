#!/bin/bash
# A computer on the emulated Beken device's home Wi-Fi (beken-emu.sh computer): this
# system's roots and the device's stub's CA trusted, so trusting the stub takes
# nothing away from any other site; then python ARGS, or a shell.
#
# Started as root, it runs them as your user (HOST_UID, HOST_GID) with the
# right to move this computer's own network links (NET_ADMIN, and NET_RAW for
# DHCP), as `wifi join` does to take it onto the device's own network
# (computer/bin/wifi, first on the PATH with the nmcli stand-in).
set -euo pipefail
if [ "$(id -u)" = 0 ] && [ -n "${HOST_UID:-}" ]; then
    : "${HOST_GID:?}"
    home=$(getent passwd "$HOST_UID" | cut -d: -f6)
    exec setpriv --reuid="$HOST_UID" --regid="$HOST_GID" --clear-groups \
        --inh-caps +net_admin,+net_raw --ambient-caps +net_admin,+net_raw \
        env HOME="${home:-/}" "$0" "$@"
fi
export PATH=/work/tools/beken/emulator/computer/bin:$PATH
cat /etc/ssl/certs/ca-certificates.crt /device/ca.crt > /tmp/ca-bundle.pem
export SSL_CERT_FILE=/tmp/ca-bundle.pem
[ $# -gt 0 ] || exec bash
exec python "$@"

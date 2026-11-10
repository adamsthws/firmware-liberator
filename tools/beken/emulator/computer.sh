#!/bin/bash
# A computer on the emulated Beken device's home Wi-Fi (beken-emu.sh computer): this
# system's roots and the device's stub's CA trusted, so trusting the stub takes
# nothing away from any other site; then python ARGS, or a shell.
set -euo pipefail
cat /etc/ssl/certs/ca-certificates.crt /device/ca.crt > /tmp/ca-bundle.pem
export SSL_CERT_FILE=/tmp/ca-bundle.pem
[ $# -gt 0 ] || exec bash
exec python "$@"

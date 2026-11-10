#!/bin/bash
# A whole conversion of the emulated plug with nobody at the keyboard, and the
# screen it drew shown at the end (tools/repo/unattended_run.py, docs/unattended.md).
#
#   tools/espressif/emulator/unattended-test.sh            from where the last run left the plug
#   tools/espressif/emulator/unattended-test.sh --fresh    the plug back to stock first, and no earlier run
#   tools/espressif/emulator/unattended-test.sh --keep     leave the emulator running afterwards
#   tools/espressif/emulator/unattended-test.sh --plug=MAC which backed-up plug (else the one run last)
#   tools/espressif/emulator/unattended-test.sh --instance=N  on emulator N, beside others (emu.sh --instance)
#   tools/espressif/emulator/unattended-test.sh --save=NAME   keep the plug as the run left it, and its
#                                                   captures (its backup, its runs), as NAME
#   tools/espressif/emulator/unattended-test.sh --from=NAME   start from a plug kept with --save: one
#                                                   converted earlier, on this computer; or a
#                                                   public one (sanitise_plug.py), with nothing
#                                                   of Sonoff's: offline, no account, no stub
#   tools/espressif/emulator/unattended-test.sh --stub-cloud  convert against the local cloud stub, with
#                                                   no eWeLink account and no .env (emu.sh --stub-cloud)
#   tools/espressif/emulator/unattended-test.sh --code=DIR    the tool in DIR - an earlier release
#                                                   (fault_suite.legacy_tree) - over the same
#                                                   captures, as updating a checkout would; it
#                                                   stays there until another --code
#   tools/espressif/emulator/unattended-test.sh --readout     a readout of the plug (cli.py readout), not a conversion
#   tools/espressif/emulator/unattended-test.sh --answers=FILE  a run as a person sits through it: its
#                                                   questions asked, and answered as FILE says
#                                                   (unattended_run.py --attended --answers)
#   tools/espressif/emulator/unattended-test.sh --api        readouts sent to the API Worker's own code
#                                                   (website/api/local.mjs), in a container of
#                                                   its own beside the emulator, not kept unsent:
#                                                   what it was asked and kept is in the
#                                                   instance's state, api/; AUTOFLASH_API_ENOUGH
#                                                   its ENOUGH
#   tools/espressif/emulator/unattended-test.sh --size=COLUMNSxROWS  the terminal the run gets (100x40):
#                                                   a short window's, say, for what the run says of it
#   tools/espressif/emulator/unattended-test.sh --timeout=MINUTES  stop the run after that long, its
#                                                   recording kept: one waiting at a question
#                                                   nothing answers, say
#   tools/espressif/emulator/unattended-test.sh -- --new   anything after -- goes to the run itself
#
# Needs .env with the eWeLink account the dump's plug is in. It starts the
# emulator with --cloud --yes: the real plug, which the emulated one is a
# clone of, must be unplugged - it is not asked. With --stub-cloud none of that
# applies: a local stub is the cloud, the plug stays offline, and the account
# is a placeholder the stub ignores.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
STATE=$ROOT/captures/emulation/state
case "$ROOT" in /mnt/[a-z]/*) STATE=$HOME/.local/share/autoflash-emu/state ;; esac
NAME=autoflash-emu PORT=8080
PLUG=192.168.60.50

emu_args=(--detach) keep=0 stub=0 run_args=() from="" save="" plug_mac="" code="" driver_args=() answers_file="" api=0
size=100x40
while [ $# -gt 0 ]; do
    case "$1" in
    --fresh|--pcap|--rebuild|--no-firewall) emu_args+=("$1") ;;
    --plug=*) emu_args+=("$1"); plug_mac=$(echo "${1#--plug=}" | tr 'A-F-' 'a-f:') ;;
    --stub-cloud) stub=1 ;;
    --instance=[1-9]|--instance=[1-9][0-9])  # another emulator beside the first: see emu.sh
        n=${1#--instance=}; emu_args+=("$1")
        NAME=autoflash-emu-$n PORT=$((8080 + n)) STATE=$STATE-$n ;;
    --keep) keep=1 ;;
    --from=*) from=${1#--from=} ;;
    --save=*) save=${1#--save=} ;;
    --code=*) code=$(cd "${1#--code=}" && pwd) ;;
    --readout) driver_args+=(--command readout) ;;
    --api) api=1 ;;
    --timeout=*) driver_args+=(--timeout "${1#--timeout=}") ;;
    --size=*) size=${1#--size=} ;;
    --answers=*) answers_file=$(cd "$(dirname "${1#--answers=}")" && pwd)/$(basename "${1#--answers=}") ;;
    --) shift; run_args=("$@"); break ;;
    *) sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
    esac
    shift
done
# Keeping the plug switches it off when the run ends; --keep leaves it on.
if [ "$keep" = 1 ] && [ -n "$save" ]; then
    echo "--save turns the plug off to keep it as the run left it: not with --keep" >&2; exit 2
fi

# A public plug (sanitise_plug.py) is past every step that reaches eWeLink, and
# runs nothing of Sonoff's: no cloud, real or stub, and no account.
SAVED=$ROOT/captures/emulation/saved
public=0
if [ -n "$from" ] && [ "$(cat "$SAVED/$from/flash-mode" 2>/dev/null)" = public ]; then
    public=1 stub=0
    emu_args+=("--plug-dir=$SAVED/$from")
    # Its MAC, so the kept flash is taken as this plug's (below).
    plug_mac=$(PYTHONPATH=$ROOT python3 -c 'import sys; from tools.espressif.flashmap import mac_of_efuse; print(mac_of_efuse(open(sys.argv[1],"rb").read()))' \
               "$SAVED/$from/efuse.bin")
fi
# The stub is its own cloud and needs no account; otherwise the real cloud is
# used, which needs the account in .env and the real plug unplugged.
if [ "$public" = 1 ]; then
    :
elif [ "$stub" = 1 ]; then
    emu_args=(--stub-cloud "${emu_args[@]}")
else
    emu_args=(--cloud --yes "${emu_args[@]}")
    [ -s "$ROOT/.env" ] || { echo "No .env with the eWeLink account - see docs/unattended.md" >&2; exit 1; }
fi

# Plugs kept with --save: the flash, the mode it was built for (a stub run's
# flash differs from a cloud run's - see emu.sh), and the captures of the runs
# that made it. A plug kept before modes were recorded is a cloud one.
mode=plain; [ "$stub" = 1 ] && mode=stub; [ "$public" = 1 ] && mode=public
if [ -n "$from" ]; then
    [ -f "$SAVED/$from/flash.bin" ] || { echo "No plug kept as '$from' (--save=$from)" >&2; exit 1; }
    kept_for=$(cat "$SAVED/$from/flash-mode" 2>/dev/null || echo plain)
    if [ "$kept_for" != "$mode" ]; then
        echo "'$from' was kept from a run $([ "$kept_for" = stub ] && echo "with" || echo "without") --stub-cloud;" \
             "carry it on the same way, or keep one with --save from a run like this one." >&2
        exit 1
    fi
    docker stop "$NAME" >/dev/null 2>&1 || true
    mkdir -p "$STATE/work"
    cp "$SAVED/$from/flash.bin" "$STATE/flash.bin"
    echo "$kept_for" > "$STATE/.flash-mode"
    rm -rf "$STATE/work/captures" && cp -a "$SAVED/$from/captures" "$STATE/work/captures"
    # The kept flash is the asked-for plug's now: emu.sh, seeing another plug
    # than ran here last, would put it by as the last one's and bring back
    # that plug's own - the run would not start from the kept plug at all.
    if [ -n "$plug_mac" ]; then
        printf '%s\n%s\n' "$SAVED/$from" "$plug_mac" > "$STATE/plug"
        rm -f "$STATE/plugs/${plug_mac//:/-}.bin" "$STATE/plugs/${plug_mac//:/-}.mode"
    fi
fi

# One left running (--keep) is carried on with as it is - the plug, its network
# and the computer's firewall - as a person running the tool again would find them.
# Not one running the other way, against the real cloud or the stub.
if [ -z "$from" ] && [[ " ${emu_args[*]} " != *" --fresh "* ]] && \
        [ -n "$(docker ps -q -f "name=^$NAME\$")" ]; then
    running=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$NAME" | sed -n 's/^STUB_CLOUD=//p')
    if [ "${running:-0}" != "$stub" ]; then
        echo "$NAME is running $([ "${running:-0}" = 1 ] && echo "against the local stub" || echo "against the real cloud")," \
             "not as asked: stop it (docker stop $NAME) or use another --instance." >&2
        exit 1
    fi
fi
if [ -n "$from" ] || [[ " ${emu_args[*]} " == *" --fresh "* ]] || \
        [ -z "$(docker ps -q -f "name=^$NAME\$")" ]; then
    "$ROOT/tools/espressif/emulator/emu.sh" "${emu_args[@]}" >/dev/null
fi
# The API Worker's own code for --api, in a container on the emulator's network,
# beside the emulated computer: nothing of this computer's - its firewall, its
# Node - comes between them. local.mjs needs Node 22.23 or newer.
API_IMAGE=node:22.23.4-bookworm-slim@sha256:5ec23b7c622b00547753bc6ffd78085dd868a68aeb41c8e0a1418f033e13d96d
api_name=$NAME-api
# shellcheck disable=SC2329  # run by the exit traps below
stop_api() {
    [ "$api" = 1 ] || return 0
    docker logs "$api_name" > "$STATE/api-server.out" 2>&1 || true
    docker rm -f "$api_name" >/dev/null 2>&1 || true
}
# shellcheck disable=SC2329  # run by the exit trap below
keep_plug() {
    [ -n "$save" ] || return 0
    docker exec "$NAME" /opt/autoflash-emu/bin/plug off >/dev/null 2>&1 || true
    rm -rf "${SAVED:?}/$save" && mkdir -p "$SAVED/$save"
    cp "$STATE/flash.bin" "$SAVED/$save/flash.bin"
    cat "$STATE/.flash-mode" 2>/dev/null > "$SAVED/$save/flash-mode" || echo plain > "$SAVED/$save/flash-mode"
    # A public plug kept is public still: its eFuses go with it.
    [ "$public" = 0 ] || cp "$STATE/efuse.bin" "$SAVED/$save/efuse.bin"
    cp -a "$STATE/work/captures" "$SAVED/$save/captures"
    rm -rf "$SAVED/$save/captures/unattended"
    echo "Kept as '$save' (--from=$save)."
}
[ "$keep" = 1 ] || trap 'stop_api; keep_plug; docker stop "$NAME" >/dev/null 2>&1 || true' EXIT

as_you=(docker exec -u "$(id -u):$(id -g)" -w /work)
waited=0
until "${as_you[@]}" "$NAME" tmux has-session -t autoflash >/dev/null 2>&1; do
    [ $((waited += 2)) -le 120 ] || { echo "The emulator did not start within 120s." >&2; exit 1; }
    sleep 2
done

# Another tool over the same captures (--code), as emu.sh copies this checkout's.
# Not tools/espressif/emulator: the emulator's own (its cloud stub, started from there)
# stays this checkout's, whatever tool it runs. Only once the emulator is up: the
# stub starts by reading the plug with this checkout's tools (seen: an older
# release's tree, swapped in first, had no tools/espressif/inspect_stock_nvs.py).
if [ -n "$code" ]; then
    # The emulator's own scripts - the plug's board, the serial log's redactor -
    # import modules from outside their folder, which the release's tree replaces:
    # one from before them stopped the plug at its next start, the error lost.
    # They take this checkout's from a copy in their folder (copy_checkout drops it).
    kept="$STATE/work/tools/espressif/emulator/checkout"
    rm -rf "$kept" && mkdir -p "$kept"
    rsync -a --exclude __pycache__/ --exclude /tools/espressif/emulator/ "$ROOT/tools" "$ROOT/devices" "$kept/"
    rsync -a --delete --exclude /captures --exclude /.venv --exclude /.python --exclude /.git \
          --exclude /.env --exclude __pycache__/ --exclude /tools/espressif/emulator/ "$code/" "$STATE/work/"
fi
# The tree's own driver, which knows where its tool is: a release from before
# the reorganisation keeps both directly in tools/.
driver=tools/repo/unattended_run.py
[ -f "$STATE/work/$driver" ] || driver=tools/unattended_run.py
# A release from before the AUTOFLASH_ names reads the emulator's settings
# under its own (S60_).
legacy_env=()
[ -f "$STATE/work/tools/s60_autoflash.py" ] && legacy_env=(-e S60_EMULATOR_URL="http://localhost:$PORT"
                                                         -e S60_EMULATOR_SUDO_PASSWORD=autoflash)
# A plug off the network - on its own setup network after a power cut, say -
# is one the run must find for itself: waited for, but not required.
waited=0
until docker exec "$NAME" ping -c1 -W1 "$PLUG" >/dev/null 2>&1; do
    if [ $((waited += 2)) -gt 120 ]; then
        echo "The emulated plug is not on the network after 120s; running anyway."
        break
    fi
    sleep 2
done
# Answers for an attended run, where the run reads them in the emulated computer.
if [ -n "$answers_file" ]; then
    mkdir -p "$STATE/work/captures"
    cp "$answers_file" "$STATE/work/captures/answers.json"
    driver_args+=(--attended --answers captures/answers.json)
fi
how=unattended
[ -n "$answers_file" ] && how="as a person would, answered from $(basename "$answers_file")"
if [[ " ${driver_args[*]} " == *" --command readout "* ]]; then
    echo "The emulated plug is up; reading it out $how..."
else
    echo "The emulated plug is up; converting it $how..."
fi

# The eWeLink account, for this run only: the emulator's copy of the
# repository has no .env, so a run you sit through asks for it. Kept in the
# container's /tmp, gone when it stops, and removed after the run anyway.
answers=/tmp/autoflash-unattended.env
if [ "$public" = 1 ]; then
    # None: a public plug is past eWeLink, and nothing of this computer's
    # (its .env, its account) may reach a run that is kept as public.
    "${as_you[@]}" "$NAME" sh -c "umask 077 && : > $answers"
elif [ "$stub" = 1 ]; then
    # A placeholder account: the stub answers the account API and ignores it.
    printf 'EWELINK_EMAIL=%s\nEWELINK_PASSWORD=%s\n' stub@autoflash-stub.test stub \
        | "${as_you[@]}" -i "$NAME" sh -c "umask 077 && cat > $answers"
else
    # The account only. Not the Wi-Fi in it, if any, nor a plug's password,
    # web login or key: those are your real network's and plugs', and the
    # emulated plug's are its own (given below). Nor the key to the sealed
    # Sonoff backup, which no run needs, nor anything else kept there: a
    # name the tool does not know stops the run.
    grep '^ *\(export \)\?EWELINK_' "$ROOT/.env" \
        | "${as_you[@]}" -i "$NAME" sh -c "umask 077 && cat > $answers"
fi
[ "$keep" = 1 ] && trap 'stop_api; docker exec "$NAME" rm -f "$answers" 2>/dev/null || true' EXIT

# On a terminal the run is shown live; otherwise its final screen is printed.
[ -t 1 ] && as_you+=(-t)
stamp=$(date +%Y%m%d-%H%M%S)
status=0
# In stub mode the tool talks to the local stub and trusts its CA.
stub_env=()
[ "$stub" = 1 ] && stub_env=(-e AUTOFLASH_EWELINK_API_BASE=https://apia.autoflash-stub.test
                             -e SSL_CERT_FILE=/run/autoflash-emu/stub-ca-bundle.pem
                             -e OPENSSL_CONF=/stub-cloud/openssl.cnf)
# A device not ready yet is taken only when this says so (tools/autoflash/kinds.py).
[ -n "${AUTOFLASH_NOT_READY_YET:-}" ] && stub_env+=(-e AUTOFLASH_NOT_READY_YET=1)
# Readouts sent to the API Worker's own code (--api), at its container's address
# on the emulator's network, or to a test server of yours; else kept unsent
# (readout.upload_url). The container goes as this script ends (stop_api).
if [ "$api" = 1 ]; then
    docker rm -f "$api_name" >/dev/null 2>&1 || true   # one a killed run left
    rm -rf "$STATE/api" "$STATE/api-server.out"
    mkdir -p "$STATE/api"
    network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}} {{end}}' "$NAME" | awk '{print $1}')
    docker run -d --name "$api_name" --network "$network" --user "$(id -u):$(id -g)" \
        -v "$ROOT/website:/website:ro" -v "$STATE/api:/api" -e AUTOFLASH_LOCAL_PROOF_BITS \
        "$API_IMAGE" node /website/api/local.mjs 0.0.0.0 8080 /api "${AUTOFLASH_API_ENOUGH:-}" >/dev/null
    api_address=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$api_name")
    waited=0
    until docker logs "$api_name" 2>&1 | grep -q '^API on'; do
        [ "$(docker inspect -f '{{.State.Running}}' "$api_name" 2>/dev/null)" = true ] && [ $((waited += 1)) -le 60 ] || {
            echo "The API's container did not start:" >&2; docker logs "$api_name" >&2 2>&1; exit 1; }
        sleep 1
    done
    AUTOFLASH_READOUT_URL=http://$api_address:8080/readout
    echo "Readouts go to the API's own code here: http://$api_address:8080 (its container $api_name), kept in ${STATE#"$ROOT/"}/api/"
fi
[ -n "${AUTOFLASH_READOUT_URL:-}" ] && stub_env+=(-e AUTOFLASH_READOUT_URL="$AUTOFLASH_READOUT_URL")
# The emulator's Wi-Fi, for a run from an ESPHome that cannot say what it is
# (one of this project's first builds); and the emulated plug's passwords,
# for one given one to test with, as this script's environment has them.
"${as_you[@]}" -e AUTOFLASH_EMULATOR_URL="http://localhost:$PORT" -e AUTOFLASH_EMULATOR_SUDO_PASSWORD=autoflash \
    "${stub_env[@]}" "${legacy_env[@]}" -e AUTOFLASH_WIFI_SSID=PICSimLabWifi -e AUTOFLASH_EMULATOR_COUNTDOWN="${AUTOFLASH_EMULATOR_COUNTDOWN:-}" \
    -e AUTOFLASH_EMULATOR_NO_AT_SAVE="${AUTOFLASH_EMULATOR_NO_AT_SAVE:-}" -e AUTOFLASH_EMULATOR_CLOUD_TURN="${AUTOFLASH_EMULATOR_CLOUD_TURN:-}" \
    -e AUTOFLASH_TASMOTA_PASSWORD="${AUTOFLASH_TASMOTA_PASSWORD:-}" -e AUTOFLASH_ESPHOME_OTA_PASSWORD="${AUTOFLASH_ESPHOME_OTA_PASSWORD:-}" \
    -e AUTOFLASH_ESPHOME_WEB_USER="${AUTOFLASH_ESPHOME_WEB_USER:-}" -e AUTOFLASH_ESPHOME_WEB_PASSWORD="${AUTOFLASH_ESPHOME_WEB_PASSWORD:-}" \
    -e AUTOFLASH_ESPHOME_API_KEY="${AUTOFLASH_ESPHOME_API_KEY:-}" \
    -e HOME=/tmp "$NAME" /opt/venv/bin/python "$driver" --size "$size" "${driver_args[@]}" \
    --save "captures/unattended/$stamp" -- --env-file "$answers" "${run_args[@]}" || status=$?

stop_api
echo "On this computer: ${STATE#"$ROOT/"}/work/captures/unattended/$stamp/"
[ "$keep" = 1 ] && echo "The emulator is still running: http://localhost:$PORT, or docker exec -it -u ubuntu $NAME tmux attach -t autoflash"
exit "$status"

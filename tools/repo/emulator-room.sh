# shellcheck shell=bash
# What every emulator script shares, sourced by tools/espressif/emulator/emu.sh,
# tools/beken/emulator/bk-emu.sh and tools/espressif/emulator/windows-test.sh.

# checkout_files SRC - the files git tracks or would in SRC, NUL-separated: what an
# emulated computer's copy of the checkout holds.
checkout_files() {
    git -C "$1" ls-files -z --cached --others --exclude-standard
}

# copy_checkout SRC DEST - the files git tracks or would (git ls-files: ignored ones
# left out, wherever the rule is: .gitignore, .git/info/exclude, the user's own), as
# the emulated computer's copy of the checkout. What SRC no longer has goes from
# DEST, but for the copy's own captures/, .venv/ and .python/.
copy_checkout() {
    local src=$1 dest=$2 list
    list=$(mktemp)
    # A list git could not give, or an empty one, would empty the copy: stop instead.
    if ! checkout_files "$src" > "$list" || [ ! -s "$list" ]; then
        echo "git could not list $src's files: its copy is left as it was" >&2
        rm -f "$list"
        return 1
    fi
    mkdir -p "$dest"
    rsync -a --from0 --files-from="$list" --ignore-missing-args "$src/" "$dest/"
    LC_ALL=C sort -z "$list" > "$list.src"
    # The checkout's top-level names, in a file: grep -q on a pipe stops its writer
    # early, which under pipefail reads as no match, and a folder the checkout has went.
    sed -z 's#/.*##' "$list.src" | LC_ALL=C sort -zu > "$list.tops"
    # A top-level folder the checkout has nothing in goes whole, made writable first:
    # an old copy's read-only toolchain would refuse a file at a time.
    local top
    while IFS= read -r -d '' top; do
        case $top in captures | .venv | .python) continue ;; esac
        if ! grep -qzxF -- "$top" "$list.tops"; then
            chmod -R u+w "$dest/$top" 2>/dev/null || true
            rm -rf "${dest:?}/$top" || echo "Could not remove $dest/$top: remove it by hand" >&2
        fi
    done < <(find "$dest" -mindepth 1 -maxdepth 1 -printf '%f\0')
    (cd "$dest" && find . \( -path ./captures -o -path ./.venv -o -path ./.python \) -prune \
        -o \( -type f -o -type l \) -print0) | sed -z 's#^\./##' | LC_ALL=C sort -z > "$list.dest"
    LC_ALL=C comm -z -23 "$list.dest" "$list.src" | (cd "$dest" && xargs -0 -r rm -f --) || true
    rm -f "$list" "$list.src" "$list.dest" "$list.tops"
}

# wait_for_room MB - until this computer has MB free and the cores for one more
# emulator: one started on a starved computer fails for that alone, its plug
# stopping mid-download. MB is what that emulator was measured to need, plus
# 100 (docs/emulator.md); AUTOFLASH_EMU_MIN_FREE_MB stands for it. After
# AUTOFLASH_EMU_ROOM_WAIT seconds (default 600) it starts anyway, saying so. Where
# /proc says nothing (macOS), it does not wait.
wait_for_room() {
    local meminfo=${AUTOFLASH_MEMINFO:-/proc/meminfo} loadavg=${AUTOFLASH_LOADAVG:-/proc/loadavg}
    local need=${AUTOFLASH_EMU_MIN_FREE_MB:-${1:?wait_for_room needs the MB this emulator needs}} limit=${AUTOFLASH_EMU_ROOM_WAIT:-600} waited=0 cores free load
    [ -r "$meminfo" ] && [ -r "$loadavg" ] || return 0
    cores=$(nproc 2>/dev/null || echo 1)
    while :; do
        free=$(awk '/^MemAvailable:/ {print int($2 / 1024)}' "$meminfo")
        load=$(cut -d' ' -f1 "$loadavg")
        if [ "${free:-0}" -ge "$need" ] && awk -v now="$load" -v cores="$cores" 'BEGIN {exit !(now < 2 * cores)}'; then
            return 0
        fi
        if [ "$waited" -ge "$limit" ]; then
            echo "Starting with ${free} MB free and a load of $load on $cores cores: a failure may be the computer's." >&2
            return 0
        fi
        [ "$waited" -gt 0 ] || echo "Waiting for room for another emulator: ${free} MB free (wanted $need), a load of $load on $cores cores..." >&2
        sleep 10
        waited=$((waited + 10))
    done
}

#!/bin/bash
# The Beken emulator's image: what it is built from, and getting it.
#
#   tools/beken/emulator/image.sh tag          the hash of what it is built from
#                                    (UPSTREAM, patches/, requirements.txt,
#                                    bk-emu.Dockerfile) - so a change to a patch
#                                    rebuilds it
#   tools/beken/emulator/image.sh build IMAGE  build it, labelled with that hash, from
#                                    the archive UPSTREAM names (downloaded once
#                                    into captures/emulation/cache/ and checked
#                                    against UPSTREAM's hash)
#   tools/beken/emulator/image.sh pull IMAGE   the published one with that hash
#                                    (ghcr.io/adamsthws/autoflash-beken-emu:<hash>,
#                                    which .github/workflows/beken-emulator-image.yml
#                                    builds), tagged IMAGE - only if its label is
#                                    that hash
#   tools/beken/emulator/image.sh ensure IMAGE [--rebuild]
#                                    IMAGE labelled with that hash: as it is, or
#                                    pulled, or built where it cannot be - one
#                                    emulator at a time, so two started together
#                                    do not both build it
#   tools/beken/emulator/image.sh source [DIR]
#                                    make the archive UPSTREAM names, in DIR
#                                    (default: the current directory), from
#                                    upstream at the pinned commit: git archive,
#                                    so the same commit gives the same bytes.
#                                    Publishing it as the release
#                                    emulator-source-beken-<commit> (one archive,
#                                    never changed), and its hash in UPSTREAM, is
#                                    how the pin moves; a change to the emulator
#                                    is a patch
#
# beken-emu.sh ensures its image every time: an image from an older checkout never
# runs unseen. AUTOFLASH_BEKEN_EMU_PUBLISHED names another place to pull from,
# AUTOFLASH_BEKEN_EMU_NO_PULL=1 always builds, and
# AUTOFLASH_BEKEN_EMU_SOURCE_URL names another place to download the archive
# from.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../../.." && pwd)
CACHE=$ROOT/captures/emulation/cache
PUBLISHED=${AUTOFLASH_BEKEN_EMU_PUBLISHED:-ghcr.io/adamsthws/autoflash-beken-emu}
# What the archive holds of upstream: the emulator, not its firmwares/.
ARCHIVED="src tests tools README.md requirements.txt .gitignore"

pin() { sed -n "s/^$1 //p" "$HERE/UPSTREAM"; }
UPSTREAM_REPO=$(pin repo)
UPSTREAM_COMMIT=$(pin commit)
ARCHIVE=$(pin archive)
ARCHIVE_SHA256=$(pin sha256)
[ -n "$UPSTREAM_REPO" ] && [ -n "$UPSTREAM_COMMIT" ] && [ -n "$ARCHIVE" ] && [ -n "$ARCHIVE_SHA256" ] \
    || { echo "$HERE/UPSTREAM: needs repo, commit, archive and sha256" >&2; exit 2; }
ARCHIVE_URL=${AUTOFLASH_BEKEN_EMU_SOURCE_URL:-https://github.com/adamsthws/firmware-liberator/releases/download/emulator-source-beken-${UPSTREAM_COMMIT:0:7}/$ARCHIVE}

tag() {
    (cd "$HERE" && find UPSTREAM requirements.txt bk-emu.Dockerfile patches -type f -print0 \
        | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -c1-16)
}

sha256() { sha256sum < "$1" | cut -c1-64; }

# The archive, downloaded once and checked - as the Espressif emulator's
# image.sh keeps the QEMU source.
archive() {
    local cache=$CACHE/$ARCHIVE part
    mkdir -p "$CACHE"
    if [ ! -f "$cache" ] || [ "$(sha256 "$cache")" != "$ARCHIVE_SHA256" ]; then
        echo "Downloading the emulator's source ($ARCHIVE, about 300 kB)..." >&2
        part=$(mktemp "$cache.XXXXXX.part")
        curl -fsSL --retry 3 -o "$part" "$ARCHIVE_URL" \
            || { rm -f "$part"; echo "Could not download $ARCHIVE_URL" >&2; exit 1; }
        mv "$part" "$cache"
    fi
    if [ "$(sha256 "$cache")" != "$ARCHIVE_SHA256" ]; then
        echo "$cache is not the archive UPSTREAM pins; delete it and run this again" >&2
        exit 1
    fi
    echo "$cache"
}

context=""
trap '[ -z "$context" ] || rm -rf "$context"' EXIT
build() {
    local source
    source=$(archive)
    context=$(mktemp -d)
    cp -r "$HERE/bk-emu.Dockerfile" "$HERE/patches" "$HERE/requirements.txt" "$context/"
    cp "$source" "$context/bekenemulator.tar.xz"
    echo "Building the Beken emulator image (the first time takes a minute or two)..." >&2
    docker build -q -t "$1" --build-arg SOURCE_SHA256="$ARCHIVE_SHA256" \
        --label "autoflash.built-from=$(tag)" \
        --label "org.opencontainers.image.source=https://github.com/adamsthws/firmware-liberator" \
        -f "$context/bk-emu.Dockerfile" "$context" >/dev/null
}

pull() {
    local want published
    want=$(tag)
    published=$PUBLISHED:$want
    docker pull -q "$published" >/dev/null 2>&1 || return 1
    if [ "$(docker image inspect -f '{{index .Config.Labels "autoflash.built-from"}}' "$published")" != "$want" ]; then
        echo "$published is not labelled $want: not used" >&2
        return 1
    fi
    docker tag "$published" "$1"
    echo "Using the published Beken emulator image ($published)." >&2
}

labelled() {
    [ "$(docker image inspect -f '{{index .Config.Labels "autoflash.built-from"}}' "$1" 2>/dev/null)" = "$(tag)" ]
}

ensure() {
    local image=$1 rebuild=${2:-}
    if command -v flock >/dev/null; then
        mkdir -p "$CACHE"
        exec 9>"$CACHE/beken-image.lock"
        flock 9
    fi
    if [ "$rebuild" != --rebuild ] && labelled "$image"; then
        return 0
    fi
    if [ "$rebuild" = --rebuild ] || [ "${AUTOFLASH_BEKEN_EMU_NO_PULL:-}" = 1 ] || ! pull "$image"; then
        build "$image"
    fi
}

source_archive() {
    local out=${1:-.} clone
    clone=$(mktemp -d)
    trap 'rm -rf "$clone"' EXIT
    echo "Cloning $UPSTREAM_REPO at ${UPSTREAM_COMMIT:0:7}..." >&2
    git init -q "$clone"
    git -C "$clone" remote add origin "$UPSTREAM_REPO"
    git -C "$clone" fetch -q --depth 1 origin "$UPSTREAM_COMMIT"
    # shellcheck disable=SC2086  # ARCHIVED is a list of paths
    git -C "$clone" -c tar.umask=0022 archive --format=tar --prefix=BekenEmulator/ "$UPSTREAM_COMMIT" $ARCHIVED \
        | xz -9 -T1 > "$out/$ARCHIVE"
    rm -rf "$clone"
    trap - EXIT
    sha256sum "$out/$ARCHIVE"
}

case "${1:-}" in
    tag) tag ;;
    ensure) [ -n "${2:-}" ] || { echo "ensure needs an image name" >&2; exit 2; }; ensure "$2" "${3:-}" ;;
    build) [ -n "${2:-}" ] || { echo "build needs an image name" >&2; exit 2; }; build "$2" ;;
    pull) [ -n "${2:-}" ] || { echo "pull needs an image name" >&2; exit 2; }; pull "$2" ;;
    source) source_archive "${2:-}" ;;
    *) sed -n "2,$(($(grep -n '^set -euo pipefail' "$0" | cut -d: -f1)-1))p" "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac

"""A build of its own, for both fault suites' ownbuild-replaced: what a device
adopted into an ESPHome builder runs once the builder has installed its build -
this project's config, built again - made without the builder's toolchain.

The tool tells such a build from a published one by when it was built, as
ESPHome's API says (routes/update.is_own_build): the same config built
elsewhere says the same project and version. So the published image, its build
time moved on an hour, is one: the time is a string in the app's data, written
once, and each family's packaging is made good around it - an S60's image
checksum and hash (imprint.repair_image), an S61's RBL and the UF2 its ESPHome
takes as an update (rbl, uf2). It is sent as a builder sends it, through
ESPHome's own update service.

    espressif(imprinted, built)  -> the S60's image, for its update service
    beken(rbl, built, name)      -> the S61's UF2, for its update service
    problem(notes, published)    -> what the scenario shows wrong, or None
"""

from __future__ import annotations

import datetime
import re

# When ESPHome says it was built, as its build writes it into the app.
BUILT = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4}")
FORMAT = "%Y-%m-%d %H:%M:%S %z"
LATER = datetime.timedelta(hours=1)


class OwnBuildError(ValueError):
    pass


def rebuilt(built: str) -> str:
    """When the build of its own says it was built: an hour after `built`, the
    same length, as a builder building the config later would say."""
    if not BUILT.fullmatch(built):
        raise OwnBuildError(f"{built!r} is not a build time as ESPHome writes one")
    return (datetime.datetime.strptime(built, FORMAT) + LATER).strftime(FORMAT)


def retimed(app: bytes, built: str) -> tuple[bytes, str]:
    """`app` with its build time `built` - written once in it - made
    rebuilt(built); and that time."""
    old = built.encode("ascii")
    if app.count(old) != 1:
        raise OwnBuildError(f"the app holds its build time {built} {app.count(old)} times, not once")
    new = rebuilt(built)
    return app.replace(old, new.encode("ascii")), new


def espressif(imprinted: bytes, built: str) -> tuple[bytes, str]:
    """An S60's build of its own, from the published image with the device's
    Wi-Fi written in (routes/espressif/images.image_for), as its update service
    takes it; and when it says it was built."""
    from tools.autoflash import imprint
    app, new = retimed(imprinted, built)
    app = imprint.repair_image(app)
    report = imprint.parse_esp_image(app)
    if not report["checksum_valid"] or report["sha256_valid"] is False:
        raise OwnBuildError("the image made again failed its own validation")
    return app, new


def beken(published: bytes, built: str, name: str) -> tuple[bytes, str]:
    """An S61's build of its own, from the published RBL, in the UF2 its
    ESPHome takes as an update (as routes/beken/onward.step_esphome_update
    sends one), named `name`; and when it says it was built."""
    from tools.beken import rbl, uf2
    header = rbl.header(published)
    app, new = retimed(rbl.unpack(published), built)
    package = rbl.pack(app, name=header.name, version=header.version, timestamp=header.timestamp, sn=header.sn)
    return uf2.write(package, name=name, version=header.version), new


def problem(notes: dict, published: str) -> str | None:
    """What a scenario's device shows wrong of its build of its own, from its
    notes: the build time it said with it on (own), after the run that must
    leave it be (refused_built) and after --replace-own-build (built_now),
    whether that run said it was a build of its own (refused), and its
    calibration before and after (calibration, calibration_now). `published`:
    when the published build was built. None when nothing is wrong."""
    if not notes.get("refused"):
        return "FAIL-NOT-REFUSED"     # taken for this project's build, or another
    if notes.get("refused_built") != notes.get("own"):
        return "FAIL-REPLACED-UNASKED"  # written over without --replace-own-build
    if notes.get("built_now") != published:
        return "FAIL-NOT-REPLACED"
    if notes.get("calibration_now") != notes.get("calibration"):
        return "FAIL-CALIBRATION-LOST"
    return None

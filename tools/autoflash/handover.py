"""A converted plug handed its next firmware: the go-ahead, asked once before
the first write of a run from Tasmota, ESPHome or OpenBeken, and an image sent
to an ESPHome as an ordinary update, whatever the plug.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from tools.autoflash import esphome_ota, kinds, output, prompts, testers, unlock
from tools.autoflash.errors import Abort, NothingWritten

if TYPE_CHECKING:
    from tools.autoflash.context import Context


def consent_to_leave(ctx: Context, what: str) -> None:
    """The go-ahead, once, before the first write of a run from a converted plug.
    Not asked again as a step that stopped is tried again by itself: seen, a
    retry asked for it, and Enter stopped the run.  A kind still wanting testers
    is said to be, in the same box."""
    if getattr(ctx, "consented", False):
        return
    output.transient_begin()
    prompts.warning_box("Before going on", [
        f"From here the device's {what} is replaced. DO NOT UNPLUG IT during the",
        "process: a power cut while its memory is rewritten could leave a device",
        "that has to be opened to repair.",
        "Only go ahead with a device you can afford to lose.",
        *testers.notice(kinds.of_run(ctx.state)),
    ])
    if not prompts.require_word(prompts.CONSENT_WORD):
        raise NothingWritten("not confirmed - nothing has been written to the device")
    output.transient_end()
    ctx.consented = True


def wait_out_lost_update() -> None:
    """Until ESPHome on the plug has given up an update cut off part way,
    which holds its update service till then."""
    output.tell("    The device's update service does not answer: it is most likely still waiting for")
    output.tell("    the rest of an update that was cut off, and gives it up within two minutes.")
    started = time.monotonic()
    while (waited := time.monotonic() - started) < esphome_ota.PLUG_DATA_TIMEOUT + 15:
        output.progress(f"waiting for the device to give it up... {waited:.0f}s")
        time.sleep(1.0)
    output.progress_clear()


def send_as_esphome_update(ctx: Context, image: bytes, what: str, port: int = esphome_ota.PORT) -> float:
    """`image` as an ordinary ESPHome update, to the ESPHome (or the mover) at
    the plug's address, on `port` - ESPHome's own on an Espressif plug,
    LibreTiny's on a Beken one.  How long the sending took."""
    host = ctx.state.input("plug_ip")
    started = time.monotonic()

    def progress(sent: int, total: int) -> None:
        output.progress(f"sending {what}: {100 * sent // total}%")

    # Asked before the go-ahead (unlock_esphome); here again only for a plug
    # that changed since, or a run sending it once more.
    waited = False
    while True:
        try:
            esphome_ota.upload(host, image, port=port, progress=progress, password=ctx.esphome_password,
                               key=ctx.esphome_key)
            break
        except esphome_ota.NeedsPassword:
            output.progress_clear()
            unlock.ask_esphome_password(ctx, host)
        except (esphome_ota.NeedsEncryption, esphome_ota.WrongKey):
            output.progress_clear()
            unlock.ask_esphome_key(ctx, host)
        except esphome_ota.Busy as exc:
            # Seen in the emulator: this computer crashed at 10% of an update and
            # ran again at once, and every send timed out until the plug gave the
            # lost one up.
            if waited:
                raise Abort(f"sending {what} to the device failed: {exc}") from exc
            output.progress_clear()
            wait_out_lost_update()
            waited = True
        except (esphome_ota.OTAError, OSError) as exc:
            raise Abort(f"sending {what} to the device failed: {exc}") from exc
        finally:
            output.progress_clear()
        started = time.monotonic()  # the time asked is not the time sending
    return round(time.monotonic() - started, 1)

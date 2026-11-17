"""The question as the tool exits: an email address, to be emailed as more
devices are supported (the website's /v1/notify, website/api/index.js).

Asked once a sitting, however many devices it converted or read out, and
whether it finished, failed or was quit with q; not where it never began (q
at its first question, or REFUSING), was stopped with Ctrl-C or its window
closed, or nobody is at the keyboard. The sign-up names the devices in the
owner's eWeLink account, seen as the sitting went (saw_devices), and the
readouts they sent (sent_readout), so their own devices' news can reach them
too. The eWeLink account's address is offered first, taken with Enter or y.

One given that could not be sent is kept too (PENDING), with what it was
to be sent with, and sent at the next sitting's end in place of the
question. Once an address is taken, it is kept in the settings (SETTING)
and never asked for again on this computer: each later sitting sends its devices and
readouts with it, unasked, and the website adds them to the address's if it
was confirmed. One removed or never confirmed is not emailed again; its
owner can sign up again on the website (SIGNUP_PAGE). The `email` command
(change) gives another address, or has this computer forget it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tools.autoflash import output, prompts, runs
from tools.ewelink.get_device_key import is_email

# Where someone can sign up instead, when the tool cannot reach the website.
SIGNUP_PAGE = "https://firmwareliberator.com/notify"
QUESTION = "Get an email as more devices are supported?"
# The settings' key for the address taken (runs.load_settings).
SETTING = "signup_email"
# And for one given that could not be sent yet: {"email", "devices", "readouts"}.
PENDING = "signup_pending"


@dataclass
class Sitting:
    """What this sitting has seen of the owner's devices and readouts."""
    devices: dict[tuple[str, str], None] = field(default_factory=dict)   # (product, firmware model), in order
    readouts: dict[str, None] = field(default_factory=dict)   # each reference sent under
    account: str = ""    # the eWeLink account signed in to, if an email address
    stopped: bool = False   # by Ctrl-C, or its window closed: not asked


SITTING = Sitting()


def saw_devices(devices: list[dict[str, Any]]) -> None:
    """The devices eWeLink lists in the owner's account."""
    for device in devices:
        product = str(device.get("productModel") or "").strip()
        if product:
            SITTING.devices[(product, str((device.get("extra") or {}).get("model") or ""))] = None


def sent_readout(reference: str) -> None:
    """A readout the website took, by its reference (none for one it already
    had enough of)."""
    if reference:
        SITTING.readouts[reference] = None


def saw_account(name: str) -> None:
    """The eWeLink account used: offered as the address, if it is one."""
    if is_email(name):
        SITTING.account = name


def stopped() -> None:
    """The sitting was stopped with Ctrl-C, or its window closed."""
    SITTING.stopped = True


def account_address() -> str:
    """The address to offer: this sitting's eWeLink account's, or else the
    last one used on this computer, if either is an email address."""
    if SITTING.account:
        return SITTING.account
    last = str(runs.load_settings().get("ewelink_email") or "")
    return last if is_email(last) else ""


def given() -> str:
    """The address taken on this computer before, if any."""
    return str(runs.load_settings().get(SETTING) or "")


def remember(email: str | None) -> None:
    """Keeps `email` as the address given; None forgets it."""
    settings = runs.load_settings()
    if email is None:
        settings.pop(SETTING, None)
    else:
        settings[SETTING] = email
    runs.save_settings(settings)


def ask_address() -> str:
    return prompts.ask_text("Your email address", private="email",
                            validate=lambda text: (is_email(text), "That does not look like an email address."))


def change() -> int:
    """The `email` command: the address kept on this computer, changed or
    forgotten. Removing it from the list is its emails' link alone: this
    computer holds nothing that could."""
    from tools.autoflash import readout   # readout imports this module
    before = given()
    if not before:
        output.say("No email address is kept on this computer: the tool asks for one as it exits.")
        return 0
    try:
        choice = prompts.ask_choice(f"News of more devices goes to {before}.",
                                    ["Keep it", "Use a different address", "Forget it on this computer"])
        if choice == 0:
            return 0
        if choice == 2:
            remember(None)
            output.say("Forgotten on this computer. To stop the emails, use the link in any of them.")
            return 0
        email = ask_address().strip()
    except (prompts.QuitRun, KeyboardInterrupt):
        return 0
    if email.lower() == before.lower():
        output.say("That is the address kept already.")
        return 0
    if not readout.notify(email, ()):
        output.say("That could not be sent just now. Try again later, or sign up at:")
        output.show_link(SIGNUP_PAGE)
        return 1
    remember(email)
    output.say(f"Check your inbox to confirm. To stop emails to {before}, use the link in any of them.")
    return 0


def ask_on_exit() -> None:
    """The question, as the sitting ends: an address, and sent; or, with one
    taken before, the sitting's devices and readouts sent with it, unasked.
    Never raises: q, n or Ctrl-C leave at once."""
    from tools.autoflash import interrupt, readout   # readout imports this module
    if SITTING.stopped or prompts.UNATTENDED or interrupt.window_closed() or readout.upload_url() is None:
        return
    before = given()
    if not before and send_pending():
        return
    if before:
        # Only added to the address if it was confirmed: one removed by its
        # owner, or never confirmed, is not emailed again.
        if (SITTING.devices or SITTING.readouts) and not readout.notify(before, SITTING.devices, SITTING.readouts,
                                                                         update=True):
            output.log_only("    This sitting's devices were not added to the email sign-up.")
        return
    address = account_address()
    options = [*([f"Yes, at {address}", "Use a different address"] if address else ["Add an email address"]),
               "No, thank you"]
    output.tell("")
    # The question and its answer stay only once the address is taken: one
    # that could not be sent leaves where to sign up instead.
    output.transient_begin()
    try:
        choice = prompts.ask_choice(f"{QUESTION} [{'Y/n' if address else 'y/n'}]", options,
                                    shortcuts={**({"": 0} if address else {}), "y": 0, "yes": 0,
                                               "n": len(options) - 1, "no": len(options) - 1})
        if choice == len(options) - 1:
            output.transient_keep()
            return
        email = address if choice == 0 and address else ask_address()
    except (prompts.QuitRun, KeyboardInterrupt):
        output.transient_keep()
        return
    if readout.notify(email.strip(), SITTING.devices, SITTING.readouts):
        output.transient_keep()
        remember(email.strip())
        output.tell("    Check your inbox to confirm.")
    else:
        output.transient_end()
        keep_pending(email.strip())
        output.tell("That could not be sent just now: it is sent as you next use this tool. Or sign up at:")
        output.show_link(SIGNUP_PAGE)


def keep_pending(email: str) -> None:
    """`email`, given but not sent, kept with this sitting's devices and
    readouts, to send as the next sitting ends (send_pending)."""
    settings = runs.load_settings()
    settings[PENDING] = {"email": email, "devices": [list(device) for device in SITTING.devices],
                         "readouts": list(SITTING.readouts)}
    runs.save_settings(settings)


def send_pending() -> bool:
    """An address given before that could not be sent then, sent now with what
    it was to be sent with and this sitting's own, in place of the question:
    True where there was one, sent or not - one not sent waits for the next."""
    from tools.autoflash import readout   # readout imports this module
    settings = runs.load_settings()
    pending = settings.get(PENDING)
    if not isinstance(pending, dict) or not is_email(str(pending.get("email") or "")):
        return False
    email = str(pending["email"])
    devices = dict.fromkeys([*(tuple(device) for device in pending.get("devices") or []), *SITTING.devices])
    readouts = dict.fromkeys([*(pending.get("readouts") or []), *SITTING.readouts])
    if not readout.notify(email, devices, readouts):
        output.log_only("    The email sign-up given before could not be sent this time either.")
        return True
    settings.pop(PENDING, None)
    settings[SETTING] = email
    runs.save_settings(settings)
    output.tell("")
    output.tell(f"The email sign-up you gave last time went through: check {email} to confirm.")
    return True

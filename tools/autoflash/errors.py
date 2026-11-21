"""What stops a run, and what is worth trying again.
"""

from __future__ import annotations

import errno
import http.client
import re
import socket
import ssl
import urllib.error


class Abort(RuntimeError):
    """What stops a run, saying what to do. `failure`, where given (or a
    subclass's own), is its class (failures.CLASSES), which its report sends
    as it is; without one its words are classed (failures.classify).
    tests/autoflash/test_failures.py holds each one's words to its class, as
    the API checks a report's words against the class it claims."""
    failure: str | None = None

    def __init__(self, *args: object, failure: str | None = None) -> None:
        super().__init__(*args)
        if failure is not None:
            self.failure = failure


class StoppedPartWay(Abort):
    """A send that stopped part way: what it got as far as writing is for the
    device to say (ATTEMPTED_EVIDENCE), so the step stays attempted."""
    failure = "upload-cut"


class CutOff(Abort):
    """A transfer to or from the device cut off part way, after the tries a
    step makes: the run ends saying what most often causes that, and how to
    go around it (runner.cut_off_hint)."""


class NothingWritten(Abort):
    """A step stopped before it wrote anything to the device: a run of nothing
    else does not say what the device is (past_the_commit)."""


class Finished(NothingWritten):
    """The run ends here by the person's choice, with nothing converted: a
    readout instead (readout.py). Its message is what the run ends by saying;
    `code`, what the run exits with: 1 for a readout that failed."""

    def __init__(self, message: str = "", code: int = 0) -> None:
        super().__init__(message)
        self.code = code


class GoBack(Exception):
    """A step finds that an earlier one did not take on the device - the image
    it sent was never installed: the run goes back and does that one again.
    `outcome`: what the stage line says of the step that found it."""

    def __init__(self, step: str, reason: str, outcome: str) -> None:
        super().__init__(reason)
        self.step, self.reason, self.outcome = step, reason, outcome


# A failure worth another go: the device or the network not answering.  A refusal
# - the device's checks, a password, the wrong model - would only say it again.
TRANSIENT_RE = re.compile(
    r"HTTP -1|timed out|timeout|sent nothing|did not answer|not answering|unreachable|"
    r"could not reach|did not take the connection|cut the connection off|"   # as plainly() says them
    r"connection (refused|reset|aborted)|ConnectionError|IncompleteRead|URLError|"
    r"No route|stopped the \w+ without|did not complete within|Wi-Fi went down|"
    r"closed the connection|Broken pipe|connection to the (plug|device) was lost",
    re.IGNORECASE,
)


# The ways a request to the device fails on the network, as the step's own
# code did not catch them: a connection refused, reset or cut off, its reply
# cut short or garbled, or no reply in time.
NETWORK_ERRORS = (ConnectionError, TimeoutError, urllib.error.URLError, http.client.HTTPException)

# What an OSError's errno says when this computer has no way to a server on
# the internet: no network, or none out of it. Windows' sockets give their own
# numbers.
NO_WAY_THERE = {errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ENETDOWN,
                *(getattr(errno, name) for name in ("WSAEHOSTUNREACH", "WSAENETUNREACH", "WSAENETDOWN")
                  if hasattr(errno, name))}


# What to check where this computer does not reach a server on the internet,
# or the device on its network (plainly): "its" and "it", after "this computer
# could not reach ...".
CHECK_INTERNET = "Check its internet connection"
CHECK_DEVICE = "Check it is switched on, and on the same network as this computer"


def plainly(exc: BaseException, what: str, check: str = CHECK_INTERNET) -> str:
    """Why `what` - "the website", "eWeLink", "the device" - could not be
    reached, said so someone can act on it, with what to `check`: "<urlopen
    error [Errno 113] No route to host>" means nothing to most. The error
    itself is for the log. Anything else, a server's own answer among it, is
    said as it is."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) and not isinstance(
        exc, urllib.error.HTTPError) else exc
    if isinstance(reason, socket.gaierror) or (isinstance(reason, OSError) and reason.errno in NO_WAY_THERE):
        return f"this computer could not reach {what}. {check}"
    if isinstance(reason, TimeoutError):
        return f"{what} did not answer in time. Try again later"
    if isinstance(reason, ssl.SSLError):
        return f"no secure connection to {what} could be made: something on this network may be in the way"
    if isinstance(reason, ConnectionRefusedError):
        return f"{what} did not take the connection. Try again later"
    if isinstance(reason, ConnectionError):
        return f"{what} cut the connection off. Try again later"
    return str(exc)

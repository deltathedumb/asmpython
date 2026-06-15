"""signal module: set handlers for asynchronous events.

In asmpython, signal handling is a stub — no signal delivery mechanism
exists in the native runtime. The module provides the constants and
SIG_DFL/SIG_IGN so that `from signal import SIGINT, signal` works.
"""
from __future__ import annotations


SIGABRT: int = 6
SIGFPE: int = 8
SIGILL: int = 4
SIGINT: int = 2
SIGSEGV: int = 11
SIGTERM: int = 15
SIGBREAK: int = 21
SIGHUP: int = 1
SIGQUIT: int = 3
SIGKILL: int = 9
SIGPIPE: int = 13
SIGALRM: int = 14
SIGUSR1: int = 10
SIGUSR2: int = 12
SIGCHLD: int = 17
SIGCONT: int = 18
SIGSTOP: int = 19
SIGTSTP: int = 20
SIGTTIN: int = 21
SIGTTOU: int = 22
SIGWINCH: int = 28

SIG_DFL: int = 0
SIG_IGN: int = 1


_handlers: list = []
_handler_sigs: list = []


def signal(signalnum: int, handler: int) -> int:
    """Set the handler for signal signalnum. Returns previous handler."""
    i: int = 0
    while i < len(_handler_sigs):
        if _handler_sigs[i] == signalnum:
            old: int = _handlers[i]
            _handlers[i] = handler
            return old
        i = i + 1
    _handler_sigs.append(signalnum)
    _handlers.append(handler)
    return SIG_DFL


def getsignal(signalnum: int) -> int:
    """Return the current signal handler for signalnum."""
    i: int = 0
    while i < len(_handler_sigs):
        if _handler_sigs[i] == signalnum:
            return _handlers[i]
        i = i + 1
    return SIG_DFL


def raise_signal(signalnum: int) -> None:
    """Send a signal to the calling process (stub: no-op)."""
    pass


def pause() -> None:
    """Wait until a signal is received (stub: no-op)."""
    pass


def alarm(time: int) -> int:
    """Schedule SIGALRM in 'time' seconds (stub: returns 0)."""
    return 0


def setitimer(which: int, seconds: float, interval: float = 0.0) -> list:
    """Set an interval timer (stub: returns [0.0, 0.0])."""
    return [0, 0]


def getitimer(which: int) -> list:
    """Return current interval timer (stub: returns [0.0, 0.0])."""
    return [0, 0]


ITIMER_REAL: int = 0
ITIMER_VIRTUAL: int = 1
ITIMER_PROF: int = 2

NSIG: int = 64

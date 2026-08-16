# expect:
# 2
# 15
# 0
# 1
# True
# True
# 0
# handled 99
# 0
# 0

from __future__ import annotations
import sys
import signal


def handler(sig: int, frame: int) -> int:
    print("handled", sig)
    return 0


print(signal.SIGINT)
print(signal.SIGTERM)
print(signal.SIG_DFL)
print(signal.SIG_IGN)

# SIGABRT/NSIG differ between the Windows and POSIX CRTs; match whichever
# real CPython reports on this platform.
if sys.platform == "win32":
    print(signal.SIGABRT == 22)
    print(signal.NSIG == 23)
else:
    print(signal.SIGABRT == 6)
    print(signal.NSIG == 65)

# signal()/getsignal() are a stub registry (no real signal delivery): the
# first call returns SIG_DFL since no handler was previously installed.
old: int = signal.signal(signal.SIGINT, handler)
print(old)

cur: int = signal.getsignal(signal.SIGINT)
cur(99, 0)

# alarm/raise_signal/pause are no-op stubs that all return/print 0.
print(signal.alarm(5))
print(signal.raise_signal(signal.SIGINT))

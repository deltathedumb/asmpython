"""getpass module: portable password input."""
from __future__ import annotations
import sys


def getpass(prompt: str = "Password: ", stream: int = 0) -> str:
    """Prompt for a password without echoing.

    In asmpython, echo suppression is not available — reads a plain line.
    """
    sys.stderr.write(prompt)
    sys.stderr.flush()
    line: str = sys.stdin.readline()
    n: int = len(line)
    if n > 0 and line[n - 1] == "\n":
        line = line[:n - 1]
    return line


def getuser() -> str:
    """Return the login name of the current user."""
    import os
    user: str = os.getenv("USERNAME")
    if user != "":
        return user
    user = os.getenv("USER")
    if user != "":
        return user
    user = os.getenv("LOGNAME")
    if user != "":
        return user
    return "unknown"

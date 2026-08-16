"""linecache: random access to text lines from files with caching.

Usage::

    import linecache

    line = linecache.getline("myfile.py", 42)   # returns '' if out of range
    lines = linecache.getlines("myfile.py")      # all lines as a list

The cache is a global dict mapping filename -> list of lines.
Call linecache.clearcache() or linecache.checkcache() to manage it.
"""
from __future__ import annotations

import os

# Global cache: filename -> list of lines (each line includes its '\n')
cache: dict = {}


def getlines(filename: str, module_globals: dict = {}) -> list:
    """Return a list of lines from *filename*, cached after the first read."""
    if filename in cache:
        return cache[filename]
    return _updatecache(filename)


def getline(filename: str, lineno: int, module_globals: dict = {}) -> str:
    """Return line *lineno* (1-based) from *filename*, or '' if missing."""
    lines: list = getlines(filename, module_globals)
    if lineno < 1 or lineno > len(lines):
        return ""
    return lines[lineno - 1]


def clearcache() -> None:
    """Clear the entire line cache."""
    global cache
    cache = {}


def checkcache(filename: str = "") -> None:
    """Discard cache entries that are no longer valid (file changed)."""
    if filename:
        if filename in cache:
            del cache[filename]
    else:
        keys: list = []
        for k in cache:
            keys.append(k)
        for k in keys:
            del cache[k]


def updatecache(filename: str, module_globals: dict = {}) -> list:
    """Update the cache for *filename* and return its lines."""
    return _updatecache(filename)


def _updatecache(filename: str) -> list:
    if not os.path.exists(filename):
        return []
    f = open(filename, "r")
    content: str = f.read()
    f.close()
    if not content:
        cache[filename] = []
        return []
    raw: list = content.split("\n")
    lines: list = []
    i: int = 0
    while i < len(raw) - 1:
        lines.append(raw[i] + "\n")
        i = i + 1
    last: str = raw[len(raw) - 1]
    if last:
        lines.append(last)
    cache[filename] = lines
    return lines


def lazycache(filename: str, module_globals: dict) -> int:
    """Register a source supplier (stub; returns 0)."""
    return 0

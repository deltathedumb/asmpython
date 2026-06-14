"""glob module: Unix shell-style pathname pattern matching.

Uses os.listdir and a simple fnmatch implementation to match patterns.
"""
from __future__ import annotations

import os


def _fnmatch(name: str, pat: str) -> int:
    """Return 1 if name matches the shell pattern pat."""
    ni: int = 0
    pi: int = 0
    nlen: int = len(name)
    plen: int = len(pat)
    while pi < plen and ni < nlen:
        pc: str = pat[pi]
        if pc == "*":
            while pi < plen and pat[pi] == "*":
                pi = pi + 1
            if pi == plen:
                return 1
            while ni <= nlen:
                if _fnmatch(name[ni:], pat[pi:]) == 1:
                    return 1
                ni = ni + 1
            return 0
        elif pc == "?":
            ni = ni + 1
            pi = pi + 1
        elif pc == "[":
            pi = pi + 1
            negate: int = 0
            if pi < plen and pat[pi] == "!":
                negate = 1
                pi = pi + 1
            matched: int = 0
            while pi < plen and pat[pi] != "]":
                if pi + 2 < plen and pat[pi + 1] == "-":
                    if name[ni] >= pat[pi] and name[ni] <= pat[pi + 2]:
                        matched = 1
                    pi = pi + 3
                else:
                    if name[ni] == pat[pi]:
                        matched = 1
                    pi = pi + 1
            if pi < plen:
                pi = pi + 1
            if negate == 1:
                matched = 1 - matched
            if matched == 0:
                return 0
            ni = ni + 1
        else:
            if name[ni] != pc:
                return 0
            ni = ni + 1
            pi = pi + 1
    while pi < plen and pat[pi] == "*":
        pi = pi + 1
    return 1 if ni == nlen and pi == plen else 0


def _split_path(path: str) -> list:
    """Split path into directory and filename parts."""
    last_sep: int = -1
    i: int = 0
    while i < len(path):
        c: str = path[i]
        if c == "/" or c == "\\":
            last_sep = i
        i = i + 1
    if last_sep < 0:
        return [".", path]
    return [path[:last_sep] if last_sep > 0 else path[0:1], path[last_sep + 1:]]


def glob(pathname: str, recursive: int = 0) -> list:
    """Return a list of paths matching pathname pattern."""
    parts: list = _split_path(pathname)
    directory: str = parts[0]
    pattern: str = parts[1]
    result: list = []
    has_magic: int = 0
    i: int = 0
    while i < len(pattern):
        c: str = pattern[i]
        if c == "*" or c == "?" or c == "[":
            has_magic = 1
        i = i + 1
    if has_magic == 0:
        if directory == ".":
            result.append(pattern)
        else:
            result.append(directory + os.sep + pattern)
        return result
    entries: list = os.listdir(directory)
    j: int = 0
    while j < len(entries):
        name: str = entries[j]
        if _fnmatch(name, pattern) == 1:
            if directory == ".":
                result.append(name)
            else:
                result.append(directory + os.sep + name)
        j = j + 1
    return result


def iglob(pathname: str, recursive: int = 0) -> list:
    """Iterator version of glob (returns list in asmpython)."""
    return glob(pathname, recursive)


def escape(pathname: str) -> str:
    """Escape special characters in a pathname for use as a literal pattern."""
    result: str = ""
    i: int = 0
    while i < len(pathname):
        c: str = pathname[i]
        if c == "*" or c == "?" or c == "[":
            result = result + "[" + c + "]"
        else:
            result = result + c
        i = i + 1
    return result

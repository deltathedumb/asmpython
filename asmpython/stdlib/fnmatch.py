"""fnmatch module: Unix filename pattern matching.

The same pattern engine as glob._fnmatch but exposed as a public module.
Supports *, ?, [seq], [!seq] patterns.
"""
from __future__ import annotations


def _match_impl(name: str, ni: int, pat: str, pi: int) -> int:
    """Recursive match of name[ni:] against pat[pi:]."""
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
                if _match_impl(name, ni, pat, pi) == 1:
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


def fnmatch(name: str, pat: str) -> int:
    """Test whether name matches pattern pat (case-sensitive)."""
    return _match_impl(name, 0, pat, 0)


def fnmatchcase(name: str, pat: str) -> int:
    """Test whether name matches pattern pat (always case-sensitive)."""
    return _match_impl(name, 0, pat, 0)


def fnfilter(names: list, pat: str) -> list:
    """Return the subset of names that match pattern pat."""
    result: list = []
    i: int = 0
    while i < len(names):
        if _match_impl(names[i], 0, pat, 0) == 1:
            result.append(names[i])
        i = i + 1
    return result


def translate(pat: str) -> str:
    """Translate a shell pattern to a regular expression string."""
    result: str = ""
    i: int = 0
    n: int = len(pat)
    while i < n:
        c: str = pat[i]
        if c == "*":
            result = result + ".*"
        elif c == "?":
            result = result + "."
        elif c == "[":
            result = result + "["
            i = i + 1
            if i < n and pat[i] == "!":
                result = result + "^"
                i = i + 1
            while i < n and pat[i] != "]":
                result = result + pat[i]
                i = i + 1
            result = result + "]"
        elif c == "." or c == "^" or c == "$" or c == "+" or c == "{" or c == "}" or c == "|" or c == "(" or c == ")":
            result = result + "\\" + c
        else:
            result = result + c
        i = i + 1
    return result + "\\Z"

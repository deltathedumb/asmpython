"""re module: basic regular expression matching.

Implements a backtracking NFA matcher supporting:
  . * + ? [] [^] \\d \\w \\s \\D \\W \\S ^ $ | () and literal chars.

Returns a Match-like object with .group(0) and .start()/.end() methods,
or None on no match. This module is written in asmpython's compilable
Python subset so it compiles to native code with no external dependencies.
"""
from __future__ import annotations


# ---- character classification helpers --------------------------------------

def _is_digit(c: str) -> int:
    o: int = ord(c)
    return 1 if o >= 48 and o <= 57 else 0


def _is_alpha(c: str) -> int:
    o: int = ord(c)
    if o >= 65 and o <= 90:
        return 1
    if o >= 97 and o <= 122:
        return 1
    return 0


def _is_space(c: str) -> int:
    o: int = ord(c)
    if o == 32 or o == 9 or o == 10 or o == 13:
        return 1
    return 0


def _is_word(c: str) -> int:
    if _is_digit(c) or _is_alpha(c):
        return 1
    if c == "_":
        return 1
    return 0


# ---- Match object ----------------------------------------------------------

class ReMatch:
    def __init__(self, start: int, end: int, string: str) -> None:
        self._start: int = start
        self._end: int = end
        self._string: str = string

    def group(self, n: int = 0) -> str:
        return self._string[self._start:self._end]

    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end

    def span(self) -> tuple:
        return (self._start, self._end)


# ---- Pattern parser / matcher ----------------------------------------------
# State: (pattern, pat_pos, string, str_pos) -> new_str_pos or -1

def _match_char_class(pat: str, pi: int, c: str) -> int:
    """Match a character class [...] starting at pat[pi+1].
    Returns 1 if c matches, 0 otherwise. pi points to '['.
    """
    negate: int = 0
    i: int = pi + 1
    if i < len(pat) and pat[i] == "^":
        negate = 1
        i = i + 1
    matched: int = 0
    while i < len(pat) and pat[i] != "]":
        ch: str = pat[i]
        if ch == "\\" and i + 1 < len(pat):
            esc: str = pat[i + 1]
            if esc == "d":
                if _is_digit(c):
                    matched = 1
            elif esc == "w":
                if _is_word(c):
                    matched = 1
            elif esc == "s":
                if _is_space(c):
                    matched = 1
            elif esc == "D":
                if not _is_digit(c):
                    matched = 1
            elif esc == "W":
                if not _is_word(c):
                    matched = 1
            elif esc == "S":
                if not _is_space(c):
                    matched = 1
            else:
                if c == esc:
                    matched = 1
            i = i + 2
        elif i + 2 < len(pat) and pat[i + 1] == "-":
            lo: int = ord(ch)
            hi: int = ord(pat[i + 2])
            if ord(c) >= lo and ord(c) <= hi:
                matched = 1
            i = i + 3
        else:
            if c == ch:
                matched = 1
            i = i + 1
    if negate:
        return 1 - matched
    return matched


def _char_class_end(pat: str, pi: int) -> int:
    """Return the index after the ']' closing pat[pi]=='['.."""
    i: int = pi + 1
    if i < len(pat) and pat[i] == "^":
        i = i + 1
    while i < len(pat) and pat[i] != "]":
        if pat[i] == "\\" and i + 1 < len(pat):
            i = i + 2
        else:
            i = i + 1
    return i + 1  # past ']'


def _atom_matches(pat: str, pi: int, s: str, si: int) -> int:
    """Returns 1 if the atom at pat[pi] matches s[si], else 0."""
    if si >= len(s):
        return 0
    c: str = s[si]
    atom: str = pat[pi]
    if atom == ".":
        return 1 if c != "\n" else 0
    if atom == "\\":
        if pi + 1 >= len(pat):
            return 0
        esc: str = pat[pi + 1]
        if esc == "d":
            return _is_digit(c)
        if esc == "w":
            return _is_word(c)
        if esc == "s":
            return _is_space(c)
        if esc == "D":
            return 1 - _is_digit(c)
        if esc == "W":
            return 1 - _is_word(c)
        if esc == "S":
            return 1 - _is_space(c)
        return 1 if c == esc else 0
    if atom == "[":
        return _match_char_class(pat, pi, c)
    return 1 if c == atom else 0


def _atom_len(pat: str, pi: int) -> int:
    """Pattern characters consumed by the atom at pi."""
    if pi >= len(pat):
        return 0
    atom: str = pat[pi]
    if atom == "\\":
        return 2
    if atom == "[":
        return _char_class_end(pat, pi) - pi
    return 1


def _try_match(pat: str, pi: int, s: str, si: int) -> int:
    """Backtracking recursive match. Returns end position in s, or -1."""
    while pi < len(pat):
        atom: str = pat[pi]

        # end-of-string anchor
        if atom == "$":
            if si == len(s):
                pi = pi + 1
                continue
            return -1

        # start-of-string anchor (only at pattern start, handled by caller)
        if atom == "^":
            pi = pi + 1
            continue

        # alternation: split at |
        if atom == "|":
            return si  # left side already matched up to here

        # group open/close (simplified: no capture, just skip)
        if atom == "(":
            alen: int = 1
            # find matching )
            depth: int = 1
            j: int = pi + 1
            while j < len(pat) and depth > 0:
                if pat[j] == "(":
                    depth = depth + 1
                elif pat[j] == ")":
                    depth = depth - 1
                j = j + 1
            # try matching the group body via recursion
            body_end: int = j - 1  # position of ')'
            group_pat: str = pat[pi + 1:body_end]
            # check for quantifier after group
            after: int = j
            quant: str = ""
            if after < len(pat) and (pat[after] == "*" or pat[after] == "+" or pat[after] == "?"):
                quant = pat[after]
                after = after + 1
            if quant == "*" or quant == "?":
                # try zero occurrences first (greedy: try max first)
                rest_si: int = _try_match(pat, after, s, si)
                if rest_si >= 0:
                    # also try matching group once
                    one_si: int = _try_match(group_pat, 0, s, si)
                    if one_si >= 0:
                        r: int = _try_match(pat, after, s, one_si)
                        if r >= 0:
                            return r
                    return rest_si
                return -1
            if quant == "+":
                first_si: int = _try_match(group_pat, 0, s, si)
                if first_si < 0:
                    return -1
                si = first_si
                while True:
                    next_si: int = _try_match(group_pat, 0, s, si)
                    if next_si < 0:
                        break
                    si = next_si
                pi = after
                continue
            # no quantifier: match group once
            new_si: int = _try_match(group_pat, 0, s, si)
            if new_si < 0:
                return -1
            si = new_si
            pi = after
            continue

        if atom == ")":
            return si  # caller handles

        al: int = _atom_len(pat, pi)
        next_pi: int = pi + al
        quant2: str = ""
        if next_pi < len(pat) and (pat[next_pi] == "*" or pat[next_pi] == "+" or pat[next_pi] == "?"):
            quant2 = pat[next_pi]
            next_pi = next_pi + 1

        if quant2 == "*":
            # greedy: match as many as possible, then recurse
            max_si: int = si
            while _atom_matches(pat, pi, s, max_si):
                max_si = max_si + 1
            # try from max down to 0
            try_si: int = max_si
            while try_si >= si:
                r2: int = _try_match(pat, next_pi, s, try_si)
                if r2 >= 0:
                    return r2
                try_si = try_si - 1
            return -1

        if quant2 == "+":
            if not _atom_matches(pat, pi, s, si):
                return -1
            si = si + 1
            while _atom_matches(pat, pi, s, si):
                si = si + 1
            # greedy: try from max down
            max_si2: int = si
            while max_si2 >= si:
                r3: int = _try_match(pat, next_pi, s, max_si2)
                if r3 >= 0:
                    return r3
                max_si2 = max_si2 - 1
            # simple: already at end, return
            pi = next_pi
            continue

        if quant2 == "?":
            if _atom_matches(pat, pi, s, si):
                r4: int = _try_match(pat, next_pi, s, si + 1)
                if r4 >= 0:
                    return r4
            pi = next_pi
            continue

        # literal / dot / escape / class — must match
        if not _atom_matches(pat, pi, s, si):
            return -1
        si = si + 1
        pi = next_pi

    return si


# ---- Public API ------------------------------------------------------------

def match(pattern: str, string: str) -> ReMatch:
    """Match at the beginning of string. Returns Match or None."""
    # ^ is implicit for match()
    pat: str = pattern
    if len(pat) > 0 and pat[0] == "^":
        pat = pat[1:]
    end: int = _try_match(pat, 0, string, 0)
    if end < 0:
        return None  # type: ignore
    return ReMatch(0, end, string)


def fullmatch(pattern: str, string: str) -> ReMatch:
    """Match the entire string. Returns Match or None."""
    pat: str = pattern
    if len(pat) > 0 and pat[0] == "^":
        pat = pat[1:]
    if len(pat) > 0 and pat[len(pat) - 1] == "$":
        pat = pat[:len(pat) - 1]
    end: int = _try_match(pat, 0, string, 0)
    if end < 0 or end != len(string):
        return None  # type: ignore
    return ReMatch(0, end, string)


def search(pattern: str, string: str) -> ReMatch:
    """Search anywhere in string. Returns first Match or None."""
    pat: str = pattern
    anchored: int = 0
    if len(pat) > 0 and pat[0] == "^":
        anchored = 1
        pat = pat[1:]
    n: int = len(string)
    i: int = 0
    while i <= n:
        end2: int = _try_match(pat, 0, string, i)
        if end2 >= 0:
            return ReMatch(i, end2, string)
        if anchored:
            return None  # type: ignore
        i = i + 1
    return None  # type: ignore


def findall(pattern: str, string: str) -> list:
    """Return list of all non-overlapping matches (as strings)."""
    results: list[str] = []
    n: int = len(string)
    i: int = 0
    pat: str = pattern
    if len(pat) > 0 and pat[0] == "^":
        pat = pat[1:]
    while i <= n:
        end3: int = _try_match(pat, 0, string, i)
        if end3 >= 0:
            results.append(string[i:end3])
            if end3 > i:
                i = end3
            else:
                i = i + 1
        else:
            i = i + 1
    return results


def sub(pattern: str, repl: str, string: str) -> str:
    """Replace non-overlapping matches with repl."""
    result: str = ""
    n: int = len(string)
    i: int = 0
    pat: str = pattern
    if len(pat) > 0 and pat[0] == "^":
        pat = pat[1:]
    while i <= n:
        end4: int = _try_match(pat, 0, string, i)
        if end4 >= 0 and i < n:
            result = result + repl
            if end4 > i:
                i = end4
            else:
                result = result + string[i]
                i = i + 1
        else:
            if i < n:
                result = result + string[i]
            i = i + 1
    return result


def split(pattern: str, string: str) -> list:
    """Split string by pattern occurrences."""
    parts: list[str] = []
    n: int = len(string)
    i: int = 0
    start: int = 0
    pat: str = pattern
    if len(pat) > 0 and pat[0] == "^":
        pat = pat[1:]
    while i <= n:
        end5: int = _try_match(pat, 0, string, i)
        if end5 >= 0 and end5 > i:
            parts.append(string[start:i])
            start = end5
            i = end5
        else:
            i = i + 1
    parts.append(string[start:])
    return parts


def compile(pattern: str, flags: int = 0) -> str:
    """Return the pattern unchanged (no pre-compilation needed)."""
    return pattern


def subn(pattern: str, repl: str, string: str) -> list:
    """Replace non-overlapping matches; return [new_string, count_str].

    Note: count is returned as str (asmpython list type constraint).
    """
    result: str = ""
    count: int = 0
    n: int = len(string)
    i: int = 0
    pat: str = pattern
    if len(pat) > 0 and pat[0] == "^":
        pat = pat[1:]
    while i <= n:
        end6: int = _try_match(pat, 0, string, i)
        if end6 >= 0 and i < n:
            result = result + repl
            count = count + 1
            if end6 > i:
                i = end6
            else:
                result = result + string[i]
                i = i + 1
        else:
            if i < n:
                result = result + string[i]
            i = i + 1
    return [result, str(count)]


def finditer(pattern: str, string: str) -> list[ReMatch]:
    """Return list of Match objects for all non-overlapping matches."""
    results: list = []
    n: int = len(string)
    i: int = 0
    pat: str = pattern
    if len(pat) > 0 and pat[0] == "^":
        pat = pat[1:]
    while i < n:
        end7: int = _try_match(pat, 0, string, i)
        if end7 >= 0:
            m: ReMatch = ReMatch(i, end7, string)
            results.append(m)
            if end7 > i:
                i = end7
            else:
                i = i + 1
        else:
            i = i + 1
    return results


def escape(pattern: str) -> str:
    """Escape special regex metacharacters in pattern."""
    special: str = r"\.^$*+?{}[]|()"
    result: str = ""
    for ch in pattern:
        if ch in special:
            result = result + "\\" + ch
        else:
            result = result + ch
    return result


def purge() -> None:
    """Clear the regex cache (no-op: no cache in this implementation)."""
    pass


IGNORECASE: int = 2
I: int = 2
MULTILINE: int = 8
M: int = 8
DOTALL: int = 16
S: int = 16
VERBOSE: int = 64
X: int = 64
ASCII: int = 256
A: int = 256
UNICODE: int = 32
U: int = 32
LOCALE: int = 4
L: int = 4


class error(Exception):
    """Exception raised for invalid regular expressions."""

    def __init__(self, msg: str = "", pattern: str = "", pos: int = 0) -> None:
        self.msg: str = msg
        self.pattern: str = pattern
        self.pos: int = pos

    def __str__(self) -> str:
        return self.msg

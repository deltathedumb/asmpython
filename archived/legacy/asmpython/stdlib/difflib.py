"""difflib module: helpers for computing deltas between sequences."""
from __future__ import annotations


def _find_lcs(a: list[str], b: list[str]) -> list[list[int]]:
    m: int = len(a)
    n: int = len(b)
    dp: list[list[int]] = []
    i: int = 0
    while i <= m:
        row: list[int] = []
        j: int = 0
        while j <= n:
            row.append(0)
            j = j + 1
        dp.append(row)
        i = i + 1
    i = m
    while i >= 1:
        j = n
        while j >= 1:
            if a[i - 1] == b[j - 1]:
                dp[i][j] = 1 + dp[i + 1][j + 1]
            elif dp[i + 1][j] >= dp[i][j + 1]:
                dp[i][j] = dp[i + 1][j]
            else:
                dp[i][j] = dp[i][j + 1]
            j = j - 1
        i = i - 1
    return dp


def _build_opcodes(a: list[str], b: list[str], dp: list[list[int]]) -> list[list[int]]:
    opcodes: list[list[int]] = []
    i: int = 0
    j: int = 0
    m: int = len(a)
    n: int = len(b)
    while i < m or j < n:
        if i < m and j < n and a[i] == b[j]:
            opcodes.append([0, i, i + 1, j, j + 1])
            i = i + 1
            j = j + 1
        elif j < n and (i >= m or dp[i][j + 1] >= dp[i + 1][j]):
            opcodes.append([1, i, i, j, j + 1])
            j = j + 1
        else:
            opcodes.append([2, i, i + 1, j, j])
            i = i + 1
    return opcodes


def get_close_matches(word: str, possibilities: list[str], n: int = 3,
                      cutoff: float = 0.6) -> list[str]:
    """Return a list of the best 'good enough' matches of word in possibilities."""
    scores: list[list[int]] = []
    for p in possibilities:
        ratio_val: float = SequenceMatcher(None, word, p).ratio()
        if ratio_val >= cutoff:
            score_int: int = int(ratio_val * 1000)
            scores.append([score_int, len(scores)])
    scores.sort()
    scores.reverse()
    result: list[str] = []
    i: int = 0
    while i < len(scores) and i < n:
        result.append(possibilities[scores[i][1]])
        i = i + 1
    return result


class SequenceMatcher:
    """Compare pairs of sequences."""

    def __init__(self, isjunk: int, a: str = "", b: str = "") -> None:
        self.a: str = a
        self.b: str = b

    def set_seqs(self, a: str, b: str) -> None:
        self.a = a
        self.b = b

    def ratio(self) -> float:
        m: int = len(self.a)
        n: int = len(self.b)
        if m == 0 and n == 0:
            return 1.0
        if m == 0 or n == 0:
            return 0.0
        matches: int = 0
        i: int = 0
        while i < m:
            j: int = 0
            while j < n:
                if self.a[i] == self.b[j]:
                    matches = matches + 1
                    break
                j = j + 1
            i = i + 1
        return 2.0 * float(matches) / float(m + n)

    def quick_ratio(self) -> float:
        return self.ratio()

    def real_quick_ratio(self) -> float:
        return self.ratio()

    def get_matching_blocks(self) -> list[list[int]]:
        result: list[list[int]] = []
        result.append([len(self.a), len(self.b), 0])
        return result

    def get_opcodes(self) -> list[list[int]]:
        if self.a == self.b:
            return [[0, 0, len(self.a), 0, len(self.b)]]
        result: list[list[int]] = []
        result.append([3, 0, len(self.a), 0, len(self.b)])
        return result


def ndiff(a: list[str], b: list[str], linejunk: int = 0,
          charjunk: int = 0) -> list[str]:
    """Compare a and b (lists of strings); return delta in ndiff format."""
    result: list[str] = []
    for line in unified_diff(a, b):
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            result.append("+ " + line[1:])
        elif line.startswith("-"):
            result.append("- " + line[1:])
        else:
            result.append("  " + line[1:])
    return result


def unified_diff(a: list[str], b: list[str], fromfile: str = "",
                 tofile: str = "", fromfiledate: str = "",
                 tofiledate: str = "", n: int = 3,
                 lineterm: str = "\n") -> list[str]:
    """Compare two sequences of lines; generate the delta as a unified diff."""
    result: list[str] = []
    if fromfile != "" or tofile != "":
        if fromfiledate != "":
            result.append("--- " + fromfile + "\t" + fromfiledate + lineterm)
        else:
            result.append("--- " + fromfile + lineterm)
        if tofiledate != "":
            result.append("+++ " + tofile + "\t" + tofiledate + lineterm)
        else:
            result.append("+++ " + tofile + lineterm)
    ai: int = 0
    bi: int = 0
    la: int = len(a)
    lb: int = len(b)
    while ai < la or bi < lb:
        if ai < la and bi < lb and a[ai] == b[bi]:
            result.append(" " + a[ai])
            ai = ai + 1
            bi = bi + 1
        elif bi < lb and (ai >= la or True):
            chunk_start: int = bi
            while bi < lb and (ai >= la or a[ai] != b[bi]):
                bi = bi + 1
            chunk_end: int = bi
            result.append("@@ -" + str(ai + 1) + " +" + str(chunk_start + 1) +
                          "," + str(chunk_end - chunk_start) + " @@" + lineterm)
            j: int = chunk_start
            while j < chunk_end:
                result.append("+" + b[j])
                j = j + 1
        else:
            result.append("-" + a[ai])
            ai = ai + 1
    return result


def context_diff(a: list[str], b: list[str], fromfile: str = "",
                 tofile: str = "", n: int = 3,
                 lineterm: str = "\n") -> list[str]:
    """Context-format diff."""
    result: list[str] = []
    if fromfile != "":
        result.append("*** " + fromfile + lineterm)
        result.append("--- " + tofile + lineterm)
    for line in unified_diff(a, b, n=n):
        if line.startswith("+"):
            result.append("> " + line[1:])
        elif line.startswith("-"):
            result.append("< " + line[1:])
        else:
            result.append(line)
    return result


def restore(delta: list[str], which: int) -> list[str]:
    """Return one of the two sequences that generated a delta."""
    result: list[str] = []
    if which == 1:
        for line in delta:
            if line.startswith("  ") or line.startswith("- "):
                result.append(line[2:])
    else:
        for line in delta:
            if line.startswith("  ") or line.startswith("+ "):
                result.append(line[2:])
    return result


def diff_bytes(dfunc: int, a: list[str], b: list[str], fromfile: str = b"",
               tofile: str = b"") -> list[str]:
    """Wrap diff function for byte strings."""
    return dfunc(a, b, fromfile, tofile)


class Differ:
    """Compare sequences of lines."""

    def __init__(self, linejunk: int = 0, charjunk: int = 0) -> None:
        pass

    def compare(self, a: list[str], b: list[str]) -> list[str]:
        return ndiff(a, b)


class HtmlDiff:
    """Create HTML diff tables."""

    def __init__(self, tabsize: int = 8, wrapcolumn: int = 0,
                 linejunk: int = 0, charjunk: int = 0) -> None:
        self.tabsize: int = tabsize
        self.wrapcolumn: int = wrapcolumn

    def make_table(self, fromlines: list[str], tolines: list[str],
                   fromdesc: str = "", todesc: str = "",
                   context: int = 0, numlines: int = 5) -> str:
        html: str = "<table>\n"
        html = html + "<tr><th>" + fromdesc + "</th><th>" + todesc + "</th></tr>\n"
        max_lines: int = len(fromlines)
        if len(tolines) > max_lines:
            max_lines = len(tolines)
        i: int = 0
        while i < max_lines:
            fl: str = fromlines[i] if i < len(fromlines) else ""
            tl: str = tolines[i] if i < len(tolines) else ""
            html = html + "<tr><td>" + fl + "</td><td>" + tl + "</td></tr>\n"
            i = i + 1
        html = html + "</table>\n"
        return html

    def make_file(self, fromlines: list[str], tolines: list[str],
                  fromdesc: str = "", todesc: str = "",
                  context: int = 0, numlines: int = 5,
                  charset: str = "utf-8") -> str:
        return "<html><body>" + self.make_table(fromlines, tolines, fromdesc, todesc) + "</body></html>"

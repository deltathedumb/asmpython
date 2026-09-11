"""Generate `unicase_table.py` from the reference implementation's data.

Beside the table it writes rather than in `tools/`, for the reason
`_gen_unicode.py` gives: a generated table nobody can regenerate is a table
nobody can check.

    python -m asmpython.objects.c._gen_unicase

TWO TABLES AND A POOL, because case mapping is two different shapes at once.

  * MOST OF IT IS A CONSTANT DELTA OVER A RUN. Latin-1, Greek and Cyrillic
    each raise and lower by a fixed offset for hundreds of code points at a
    time, so a run holds four deltas and covers them all. 2929 code points
    with a mapping compress to 1344 runs this way.

  * A HUNDRED OF THEM GROW. `ß` uppercases to `SS` and `ﬁ` to `FI` -- one
    character becoming two or three, which no delta can say. Those get a row
    of their own whose four fields point into a shared pool of code points.

ASCII IS NOT IN IT, as in the class table: the case transforms decide those
in code, and dropping them removes the densest and least interesting part.
"""
import pathlib

HI = 0x110000


def mappings(cp: int):
    """The four full case mappings of one code point, as strings.

    THE REFERENCE IMPLEMENTATION IS ASKED rather than a rule derived from
    categories, for the same reason the class table asks it: `ﬁ` has no
    single uppercase and `İ` lowercases to two code points, and no rule short
    of the real data has those.
    """
    ch = chr(cp)
    return (ch.upper(), ch.lower(), ch.title(), ch.casefold())


def simple_deltas(cp: int):
    """The four deltas, or None if any mapping is not one code point."""
    out = []
    for m in mappings(cp):
        if len(m) != 1:
            return None
        out.append(ord(m) - cp)
    return tuple(out)


def tables():
    """The runs, the expansion rows and the pool they point into."""
    runs, expansions = [], []
    cur = start = prev = None
    for cp in range(0x80, HI):
        d = simple_deltas(cp)
        if d is None:
            expansions.append(cp)
            d = None
        if d != cur:
            if cur is not None:
                runs.append((start, prev) + cur)
            cur = d
            start = cp
        if d is not None:
            prev = cp
    if cur is not None:
        runs.append((start, prev) + cur)
    # A ZERO RUN IS NOT A MAPPING. `simple_deltas` answers (0,0,0,0) for a
    # character that cases to itself, and those are the great majority.
    runs = [r for r in runs if r[2:] != (0, 0, 0, 0)]

    pool, rows = [], []
    for cp in expansions:
        fields = []
        for m in mappings(cp):
            want = [ord(c) for c in m]
            # SHARED WHERE IT REPEATS: `ss` is the fold of both `ß` and the
            # two-character ligatures, and one copy serves all of them.
            at = find(pool, want)
            if at < 0:
                at = len(pool)
                pool.extend(want)
            fields.append((at << 8) | len(want))
        rows.append((cp,) + tuple(fields))
    return runs, rows, pool


def cased(cp: int) -> bool:
    """Is this code point CASED, in the sense the final-sigma rule means?

    ASKED OF THE REFERENCE IMPLEMENTATION rather than read off a category,
    because the property is Lu + Ll + Lt + Other_Lowercase + Other_Uppercase
    and Python exposes no list of the last two. A capital sigma at the END of
    a string lowercases to the final form exactly when the character before
    it is cased -- so putting one there and looking at the answer asks the
    question directly.
    """
    return (chr(cp) + "\u03a3").lower().endswith("\u03c2")


def ignorable(cp: int) -> bool:
    """Is this code point CASE-IGNORABLE? Asked the same way.

    The property is Mn + Me + Cf + Lm + Sk plus three word-break classes, and
    it matters because `"ΟΣ."` lowercases to `"ος."`: the full stop does not
    stop the sigma being final. Here the sigma has a cased character before it
    and a cased character two along, so it takes the final form exactly when
    what sits between is NOT ignorable.
    """
    return ("\u03b1\u03a3" + chr(cp) + "\u03b1").lower()[1] == "\u03c3"


def flag_runs():
    """Runs of code points sharing a cased/case-ignorable pair."""
    out, cur, start, prev = [], 0, 0x80, 0x80
    for cp in range(0x80, HI):
        f = (1 if cased(cp) else 0) | (2 if ignorable(cp) else 0)
        if f != cur:
            if cur:
                out.append((start, prev, cur))
            cur, start = f, cp
        prev = cp
    if cur:
        out.append((start, HI - 1, cur))
    return out


def find(pool, want):
    """Where `want` already sits in `pool`, or -1."""
    n = len(want)
    for i in range(len(pool) - n + 1):
        if pool[i:i + n] == want:
            return i
    return -1


def wrap(lines, prefix, pieces):
    line = prefix
    for piece in pieces:
        if len(line) + len(piece) > 78:
            lines.append(line)
            line = prefix
        line += piece
    lines.append(line)


def emit(into) -> str:
    runs, rows, pool = tables()
    lines = [
        "/* THE UNICODE CASE MAPPINGS, as runs of code points and a pool.",
        "",
        "   GENERATED by `objects/c/_gen_unicase.py` from the reference",
        "   implementation's own data. See that file for the shape; the short",
        "   version is that most of case mapping is a constant delta over a",
        "   run and about a hundred code points GROW instead, which no delta",
        f"   can say. {len(runs)} runs and {len(rows)} expansions.",
        "",
        "   ASCII IS DECIDED IN CODE and the table starts at U+0080. */",
        "static const struct { unsigned from, to; int dup, dlow, dtit, dfold; }",
        "apy_ucase[] = {",
    ]
    wrap(lines, "   ", [" {%d,%d,%d,%d,%d,%d}," % r for r in runs])
    lines.append("};")
    lines.append("")
    lines.append("/* A CHARACTER THAT GROWS. Each field is `(offset << 8) |")
    lines.append("   length` into the pool below. */")
    lines.append("static const struct { unsigned cp, up, low, tit, fold; }")
    lines.append("apy_ucase_x[] = {")
    wrap(lines, "   ", [" {%d,%u,%u,%u,%u}," % r for r in rows])
    lines.append("};")
    lines.append("")
    lines.append("static const unsigned apy_ucase_pool[] = {")
    wrap(lines, "   ", [" %d," % cp for cp in pool])
    lines.append("};")
    lines.append("")
    flags = flag_runs()
    lines.append("/* CASED (1) and CASE-IGNORABLE (2), which the final-sigma")
    lines.append("   rule is written in terms of and nothing else needs. */")
    lines.append("#define APY_CASED 1u")
    lines.append("#define APY_CASE_IGNORABLE 2u")
    lines.append("static const struct { unsigned from, to, flags; }")
    lines.append("apy_ucase_f[] = {")
    wrap(lines, "   ", [" {%d,%d,%u}," % r for r in flags])
    lines.append("};")
    lines.append(BODY)
    body = "\n".join(lines) + "\n"
    into.write_text(
        '"""The Unicode case mappings, as C.\n\n'
        "GENERATED by `objects/c/_gen_unicase.py`. Its own module for the\n"
        "reason `unicode_table.py` is: it is a table, and `objects.py` is\n"
        "meant to be read.\n"
        '"""\n\nUNICASE_C = r"""' + body + '"""\n',
        encoding="utf-8")
    return (f"{len(runs)} runs, {len(rows)} expansions, {len(pool)} pool, "
            f"{len(flags)} flag runs")


BODY = '''
/* How `cp` cases under `mode`, written into `out` as code points.

   ANSWERS A COUNT, because a case mapping is one-to-MANY: `ß` uppercases to
   two characters and `ﬃ` to three, so a caller that assumed one would be
   right about Latin and wrong about the rest. Four is the widest any mapping
   is, and `out` must have room for it.

   THE EXPANSIONS ARE SEARCHED FIRST because a code point in that table has
   no delta at all -- it was left out of the runs, so the run search would
   answer "no mapping" and hand back the input unchanged, which is exactly
   the bug this table exists to fix. */
static int apy_ucase_map(uint32_t cp, int mode, uint32_t *out) {
    int lo = 0, hi, mid;
    /* ASCII IS DECIDED HERE, as in the class table: the range is dense, its
       rule is one comparison and a constant, and leaving it out is what
       makes the table three thousand runs rather than a hundred thousand.
       Upper and title agree across it; lower and fold do too. */
    if (cp < 0x80) {
        out[0] = cp;
        if (mode == 0 || mode == 2) {
            if (cp >= 'a' && cp <= 'z') out[0] = cp - 32;
        } else if (cp >= 'A' && cp <= 'Z') out[0] = cp + 32;
        return 1;
    }
    hi = (int)(sizeof apy_ucase_x / sizeof apy_ucase_x[0]) - 1;
    while (lo <= hi) {
        mid = (lo + hi) / 2;
        if (cp < apy_ucase_x[mid].cp) hi = mid - 1;
        else if (cp > apy_ucase_x[mid].cp) lo = mid + 1;
        else {
            unsigned f = mode == 0 ? apy_ucase_x[mid].up
                       : mode == 1 ? apy_ucase_x[mid].low
                       : mode == 2 ? apy_ucase_x[mid].tit
                                   : apy_ucase_x[mid].fold;
            unsigned at = f >> 8, n = f & 0xFF, i;
            for (i = 0; i < n; i++) out[i] = apy_ucase_pool[at + i];
            return (int)n;
        }
    }
    lo = 0;
    hi = (int)(sizeof apy_ucase / sizeof apy_ucase[0]) - 1;
    while (lo <= hi) {
        mid = (lo + hi) / 2;
        if (cp < apy_ucase[mid].from) hi = mid - 1;
        else if (cp > apy_ucase[mid].to) lo = mid + 1;
        else {
            int d = mode == 0 ? apy_ucase[mid].dup
                  : mode == 1 ? apy_ucase[mid].dlow
                  : mode == 2 ? apy_ucase[mid].dtit
                              : apy_ucase[mid].dfold;
            out[0] = (uint32_t)((int64_t)cp + d);
            return 1;
        }
    }
    out[0] = cp;
    return 1;
}

/* The cased / case-ignorable flags of one code point. ASCII is decided here:
   a letter is cased and an apostrophe is ignorable, which is the whole of
   what the range contributes. */
static unsigned apy_ucase_flags(uint32_t cp) {
    int lo = 0, hi, mid;
    if (cp < 0x80) {
        if ((cp >= 'a' && cp <= 'z') || (cp >= 'A' && cp <= 'Z'))
            return APY_CASED;
        return (cp == '\\'' || cp == '.' || cp == ':') ? APY_CASE_IGNORABLE : 0;
    }
    hi = (int)(sizeof apy_ucase_f / sizeof apy_ucase_f[0]) - 1;
    while (lo <= hi) {
        mid = (lo + hi) / 2;
        if (cp < apy_ucase_f[mid].from) hi = mid - 1;
        else if (cp > apy_ucase_f[mid].to) lo = mid + 1;
        else return apy_ucase_f[mid].flags;
    }
    return 0;
}
'''


if __name__ == "__main__":
    print(emit(pathlib.Path(__file__).resolve().parent / "unicase_table.py"))

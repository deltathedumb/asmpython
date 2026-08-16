"""Generate `bundled/unicodedata.py` from the reference implementation's data.

    python -m asmpython.frontends.python._gen_unicodedata

Beside the module it writes rather than inside `bundled/`, which is spliced
into programs -- a generator in there would be a module a program could
import.

WHY A BUNDLED MODULE AND NOT RUNTIME C. The tables are large and almost no
program wants them: `unicode_table.py` is spliced into EVERY compiled binary,
and normalisation data is four times its size. A bundled module costs nothing
until a program says `import unicodedata`, and then costs exactly what the
data weighs. The character CLASSES stay in the C because `str.isupper` needs
them in every program; the DECOMPOSITIONS do not.

WHAT IS GENERATED and what is written by hand: the tables below the marker are
this script's, and the algorithms above it are ordinary Python kept in
`_unicodedata_body.py`. Splitting them means the code can be read and edited
without a generated file being rewritten around it.
"""
import pathlib
import unicodedata

#: Hangul is ALGORITHMIC and stays out of the table -- eleven thousand
#: syllables whose decomposition is arithmetic on the code point. Keeping them
#: would make the table five times larger and say nothing.
SBASE, SCOUNT = 0xAC00, 11172

#: One character per category index. NOT `chr(65 + i)`: at index 27 that is a
#: BACKSLASH, which starts an escape in the string literal this goes into --
#: the file compiled with a SyntaxWarning and the table read back wrong from
#: there on. Letters and digits only, none of which can escape anything.
ALPHABET = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            "abcdefghijklmnopqrstuvwxyz0123456789")


def categories():
    """Every general category, and the runs of code points holding each.

    RUNS, because a category is a property of a BLOCK far more often than of
    a character: four thousand runs cover the million and a bit code points.
    """
    names, runs = [], []
    prev = None
    for cp in range(0x110000):
        cat = unicodedata.category(chr(cp))
        if cat != prev:
            if cat not in names:
                names.append(cat)
            runs.append((cp, names.index(cat)))
            prev = cat
    return names, runs


def combining():
    """The canonical combining class, as runs. Nearly everything is zero."""
    runs, prev = [], None
    for cp in range(0x110000):
        ccc = unicodedata.combining(chr(cp))
        if ccc != prev:
            runs.append((cp, ccc))
            prev = ccc
    return runs


def decompositions():
    """Every decomposition mapping, canonical and compatibility.

    THE PRIMARY MAPPING, not the full one: `decomposition()` reports it, and
    normalisation applies it repeatedly, so storing the recursive answer would
    be a second table that says the same thing at four times the size.
    """
    tags, out = [], []
    for cp in range(0x110000):
        if SBASE <= cp < SBASE + SCOUNT:
            continue
        text = unicodedata.decomposition(chr(cp))
        if not text:
            continue
        parts = text.split()
        tag = ""
        if parts[0].startswith("<"):
            tag = parts[0]
            parts = parts[1:]
        if tag and tag not in tags:
            tags.append(tag)
        out.append((cp, tags.index(tag) + 1 if tag else 0,
                    [int(x, 16) for x in parts]))
    return tags, out


def compositions(decomp):
    """Which pairs actually compose, asked of the reference implementation.

    NOT DERIVED FROM THE DECOMPOSITIONS. A canonical pair does not always
    compose back: the composition exclusions, the singletons and the
    non-starter decompositions are all left out, and `normalize` is the only
    thing that knows the whole rule. Asking it is both shorter and right.
    """
    out = []
    for cp, tag, seq in decomp:
        if tag or len(seq) != 2:
            continue
        if unicodedata.normalize("NFC", chr(seq[0]) + chr(seq[1])) == chr(cp):
            out.append((seq[0], seq[1], cp))
    return out


def _wrapped(text: str, width: int = 72) -> str:
    """One long string as adjacent literals, so no line runs off the page."""
    lines = []
    for at in range(0, len(text), width):
        lines.append('    "' + text[at:at + width] + '"')
    return "\n".join(lines) if lines else '    ""'


def emit(body: pathlib.Path, into: pathlib.Path) -> str:
    names, cats = categories()
    ccc = combining()
    tags, decomp = decompositions()
    comp = compositions(decomp)

    parts = []
    assert len(names) <= len(ALPHABET), "more categories than letters"
    parts.append("#: The general categories, in the order the runs index "
                 "them.")
    parts.append("_CAT_NAMES = %r" % " ".join(names))
    parts.append("#: One character per index, none of which can escape "
                 "anything")
    parts.append("#: inside the string literal below.")
    parts.append("_CAT_KEY = %r" % ALPHABET)
    parts.append("")
    parts.append("#: Where each run starts, in hex, and which category it "
                 "is. Two")
    parts.append("#: parallel tables rather than one of pairs: the starts "
                 "are searched")
    parts.append("#: and the categories are only ever read at the index the "
                 "search found.")
    parts.append("_CAT_AT = (")
    parts.append(_wrapped(" ".join("%x" % at for at, _ in cats)))
    parts.append(")")
    parts.append("_CAT_OF = (")
    parts.append(_wrapped("".join(ALPHABET[i] for _, i in cats)))
    parts.append(")")
    parts.append("")
    parts.append("#: The canonical combining class, the same way. Zero for "
                 "all but a")
    parts.append("#: few hundred runs, which is why this is runs and not a "
                 "map.")
    parts.append("_CCC_AT = (")
    parts.append(_wrapped(" ".join("%x" % at for at, _ in ccc)))
    parts.append(")")
    parts.append("_CCC_OF = (")
    parts.append(_wrapped(" ".join("%x" % v for _, v in ccc)))
    parts.append(")")
    parts.append("")
    parts.append("#: The compatibility tags, in the order the mappings index")
    parts.append("#: them -- one-based, because 0 means canonical.")
    parts.append("_TAGS = %r" % " ".join(tags))
    parts.append("")
    parts.append("#: Every decomposition mapping: code point, tag, then the")
    parts.append("#: characters it maps to, all in hex, one mapping per "
                 "semicolon.")
    parts.append("_DECOMP = (")
    parts.append(_wrapped(";".join(
        "%x %x %s" % (cp, tag, " ".join("%x" % c for c in seq))
        for cp, tag, seq in decomp)))
    parts.append(")")
    parts.append("")
    parts.append("#: Every pair that COMPOSES, which is not every canonical")
    parts.append("#: pair -- see `compositions` in the generator.")
    parts.append("_COMPOSE = (")
    parts.append(_wrapped(";".join("%x %x %x" % three for three in comp)))
    parts.append(")")

    text = body.read_text(encoding="utf-8")
    marker = "# @TABLES@"
    assert marker in text, "the hand-written body has no table marker"
    text = text.replace(
        marker,
        "# GENERATED BELOW HERE by `_gen_unicodedata.py`. Everything above is\n"
        "# hand-written; nothing below is.\n"
        "unidata_version = %r\n\n" % unicodedata.unidata_version
        + "\n".join(parts))
    into.write_text(text, encoding="utf-8")
    return (f"{len(cats)} category runs, {len(ccc)} combining runs, "
            f"{len(decomp)} mappings, {len(comp)} compositions, "
            f"{len(text)} bytes")


if __name__ == "__main__":
    here = pathlib.Path(__file__).resolve().parent
    print(emit(here / "_unicodedata_body.py", here / "bundled" /
               "unicodedata.py"))

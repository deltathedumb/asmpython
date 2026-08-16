"""Unicode character properties and normalisation.

WHAT IS HERE: `normalize` in all four forms, `category`, `combining`,
`decomposition`, `is_normalized`, and the numeric properties. What is NOT is
`name` and `lookup` -- the name table is over a megabyte and answers a
question about spelling rather than about text, and carrying it would cost
every program that wants NFC a table it will never read.

THE TABLES ARE GENERATED, by `_gen_unicodedata.py`, from the reference
implementation itself. The algorithms are UAX #15's and are written out here:
decompose fully, put the combining marks in canonical order, and for the
composing forms walk the result recombining what is not blocked.

HANGUL IS ARITHMETIC and is not in the tables. Eleven thousand syllables
decompose by division and compose by multiplication, and a table of them would
be five times the size of everything else while saying nothing a formula does
not.
"""

#: Filled on first use -- see `_tables`. A module-level dict because the
#: alternative is parsing sixty kilobytes at program start whether or not
#: anything asks a question about a character.
_ready = {}

_SBASE = 0xAC00
_LBASE = 0x1100
_VBASE = 0x1161
_TBASE = 0x11A7
_LCOUNT = 19
_VCOUNT = 21
_TCOUNT = 28
_NCOUNT = 588
_SCOUNT = 11172


def _tables():
    """The tables, parsed once.

    Everything is text in the module because a literal list of six thousand
    tuples is six thousand objects to build before `main` runs; splitting a
    string builds them only when something asks.
    """
    if _ready:
        return _ready
    _ready["cat_at"] = [int(x, 16) for x in _CAT_AT.split()]
    _ready["cat_of"] = _CAT_OF
    _ready["cat_key"] = _CAT_KEY
    _ready["cat_names"] = _CAT_NAMES.split()
    _ready["ccc_at"] = [int(x, 16) for x in _CCC_AT.split()]
    _ready["ccc_of"] = [int(x, 16) for x in _CCC_OF.split()]
    canon = {}
    compat = {}
    tags = {}
    for entry in _DECOMP.split(";"):
        fields = entry.split()
        cp = int(fields[0], 16)
        tag = int(fields[1], 16)
        seq = []
        for one in fields[2:]:
            seq.append(int(one, 16))
        if tag:
            compat[cp] = seq
            tags[cp] = tag
        else:
            canon[cp] = seq
    _ready["canon"] = canon
    _ready["compat"] = compat
    _ready["tags"] = tags
    _ready["tag_names"] = _TAGS.split()
    compose = {}
    for entry in _COMPOSE.split(";"):
        fields = entry.split()
        compose[(int(fields[0], 16) << 21) | int(fields[1], 16)] = int(
            fields[2], 16)
    _ready["compose"] = compose
    return _ready


def _run_index(starts, cp):
    """Which run holds `cp` -- a binary search, so a predicate over a long
    string is one lookup per character rather than a walk."""
    lo = 0
    hi = len(starts) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if starts[mid] <= cp:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _one(ch, which):
    """The code point of a one-character string, or a TypeError."""
    if not isinstance(ch, str) or len(ch) != 1:
        raise TypeError(which + "() argument must be a unicode character")
    return ord(ch)


def category(ch):
    """The general category, as its two-letter name -- `Lu`, `Nd`, `Zs`."""
    t = _tables()
    at = _run_index(t["cat_at"], _one(ch, "category"))
    return t["cat_names"][t["cat_key"].index(t["cat_of"][at])]


def combining(ch):
    """The canonical combining class. Zero for everything that is not a
    mark, which is nearly everything."""
    t = _tables()
    return t["ccc_of"][_run_index(t["ccc_at"], _one(ch, "combining"))]


def _ccc(cp):
    t = _tables()
    return t["ccc_of"][_run_index(t["ccc_at"], cp)]


def decomposition(ch):
    """The decomposition mapping as CPython spells it: the tag, if there is
    one, then the code points in hex. Empty when there is no mapping."""
    t = _tables()
    cp = _one(ch, "decomposition")
    if _SBASE <= cp < _SBASE + _SCOUNT:
        out = []
        for one in _hangul_decompose(cp):
            out.append("%04X" % one)
        return " ".join(out)
    seq = t["canon"].get(cp)
    prefix = ""
    if seq is None:
        seq = t["compat"].get(cp)
        if seq is None:
            return ""
        prefix = t["tag_names"][t["tags"][cp] - 1] + " "
    out = []
    for one in seq:
        out.append("%04X" % one)
    return prefix + " ".join(out)


def _hangul_decompose(cp):
    """A syllable as its jamo. Arithmetic, not a table."""
    i = cp - _SBASE
    lead = _LBASE + i // _NCOUNT
    vowel = _VBASE + (i % _NCOUNT) // _TCOUNT
    trail = _TBASE + i % _TCOUNT
    if trail == _TBASE:
        return [lead, vowel]
    return [lead, vowel, trail]


def _hangul_compose(a, b):
    """Two jamo as a syllable, or -1. The other half of the arithmetic."""
    if _LBASE <= a < _LBASE + _LCOUNT and _VBASE <= b < _VBASE + _VCOUNT:
        return _SBASE + ((a - _LBASE) * _VCOUNT + (b - _VBASE)) * _TCOUNT
    if (_SBASE <= a < _SBASE + _SCOUNT and (a - _SBASE) % _TCOUNT == 0
            and _TBASE < b < _TBASE + _TCOUNT):
        return a + (b - _TBASE)
    return -1


def _decompose(text, compat):
    """Every character replaced by its mapping, REPEATEDLY.

    A mapping may itself decompose -- U+1E69 maps to U+1E63 and a dot above,
    and U+1E63 maps again -- so this is a fixed point and not one pass. The
    table holds the primary mappings, which is what `decomposition()` reports;
    doing the recursion here means one table rather than two.
    """
    t = _tables()
    out = []
    pending = []
    for ch in text:
        pending.append(ord(ch))
    at = 0
    while at < len(pending):
        cp = pending[at]
        at = at + 1
        if _SBASE <= cp < _SBASE + _SCOUNT:
            for one in _hangul_decompose(cp):
                out.append(one)
            continue
        seq = t["canon"].get(cp)
        if seq is None and compat:
            seq = t["compat"].get(cp)
        if seq is None:
            out.append(cp)
            continue
        # BACK ONTO THE FRONT OF THE QUEUE, in order, so the pieces are
        # themselves decomposed before anything after them is looked at.
        rest = pending[at:]
        pending = seq + rest
        at = 0
    return _reorder(out)


def _reorder(cps):
    """Canonical ordering: the combining marks after a starter sorted by
    class, STABLY -- two marks of the same class keep the order they were
    written, and swapping them would change the text."""
    i = 1
    while i < len(cps):
        here = _ccc(cps[i])
        if here != 0:
            j = i
            while j > 0 and _ccc(cps[j - 1]) > here:
                cps[j], cps[j - 1] = cps[j - 1], cps[j]
                j = j - 1
        i = i + 1
    return cps


def _compose_pair(a, b):
    made = _hangul_compose(a, b)
    if made >= 0:
        return made
    return _tables()["compose"].get((a << 21) | b, -1)


def _compose(cps):
    """UAX #15's composition walk.

    A character composes onto the last STARTER unless something between them
    BLOCKS it -- which is what `last` tracks: a preceding character of the
    same or a higher combining class stands in the way, and so does another
    starter. Getting that wrong silently reorders text that has two marks on
    one base, which is exactly the case normalisation exists for.
    """
    if not cps:
        return cps
    out = [cps[0]]
    starter = 0
    last = _ccc(cps[0])
    if last != 0:
        # A text beginning with a mark has no starter to compose onto, and
        # 256 is above every real class, so nothing will.
        last = 256
    i = 1
    while i < len(cps):
        cp = cps[i]
        here = _ccc(cp)
        made = -1
        if last < here or last == 0:
            made = _compose_pair(out[starter], cp)
        if made >= 0:
            out[starter] = made
            # `last` IS NOT TOUCHED: the composed character takes the
            # starter's place, so nothing about what blocks what has changed.
        else:
            out.append(cp)
            if here == 0:
                starter = len(out) - 1
            last = here
        i = i + 1
    return out


def normalize(form, text):
    """`NFC`, `NFD`, `NFKC` or `NFKD`."""
    if form != "NFC" and form != "NFD" and form != "NFKC" and form != "NFKD":
        raise ValueError("invalid normalization form")
    compat = form == "NFKC" or form == "NFKD"
    cps = _decompose(text, compat)
    if form == "NFC" or form == "NFKC":
        cps = _compose(cps)
    out = []
    for cp in cps:
        out.append(chr(cp))
    return "".join(out)


def is_normalized(form, text):
    """Whether normalising would change it. The quick check UAX #15 describes
    needs a fifth table; this is the honest answer at the cost of doing the
    work."""
    return normalize(form, text) == text


def decimal(ch, default=None):
    """The decimal value of a digit character."""
    if ch.isdecimal():
        return int(ch)
    if default is None:
        raise ValueError("not a decimal")
    return default


def digit(ch, default=None):
    if ch.isdigit():
        return int(ch)
    if default is None:
        raise ValueError("not a digit")
    return default


def numeric(ch, default=None):
    """The numeric value, which is a FLOAT and may be a fraction -- `½` is
    0.5 and is neither a digit nor a decimal."""
    if ch.isdecimal() or ch.isdigit():
        return float(int(ch))
    if default is None:
        raise ValueError("not a numeric character")
    return default


# @TABLES@

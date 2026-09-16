"""Decoding the four kinds of C literal, in one place because two stages need
the same answer.

The preprocessor evaluates `#if 'a' == 97` and the parser evaluates `char c =
'a';`, and if those two disagree about what `'\\x41'` means then a program can
be compiled differently from the way it was configured. So escape decoding, the
suffix rules and the integer-type selection live here, are written once, and
are called from both.

WHAT A LITERAL'S TYPE IS is part of decoding and not a separate question. C
gives an unsuffixed decimal constant the first of `int`, `long`, `long long`
that fits, and an unsuffixed OCTAL OR HEX constant the first of `int`,
`unsigned int`, `long`, `unsigned long`, `long long`, `unsigned long long`.
That asymmetry is why `0xFFFFFFFF` is unsigned and `4294967295` is a `long`,
which is in turn why `-1 < 0xFFFFFFFFu` is false, and a compiler that returns
only the value has thrown away the half of the answer that decides that.
"""
from __future__ import annotations

from dataclasses import dataclass

#: `\a` and friends. `\e` is a GNU extension and is accepted: it appears in
#: real headers and refusing it buys nothing.
_SIMPLE_ESCAPES = {
    "'": 0x27, '"': 0x22, "?": 0x3F, "\\": 0x5C,
    "a": 0x07, "b": 0x08, "f": 0x0C, "n": 0x0A, "r": 0x0D, "t": 0x09,
    "v": 0x0B, "e": 0x1B, "0": 0x00,
}

_HEX = "0123456789abcdefABCDEF"


class LiteralError(ValueError):
    """A literal that cannot be decoded. Carries an offset into its spelling
    so the caller can point the caret at the escape rather than the quote."""

    def __init__(self, message: str, offset: int = 0, code: str = "E1010") -> None:
        super().__init__(message)
        self.offset = offset
        self.code = code


# ── escape sequences ────────────────────────────────────────────────────────
def decode_escapes(body: str, *, max_value: int,
                   literal: list[bool] | None = None) -> list[int]:
    """The character values of `body`, escapes resolved.

    `max_value` is the widest value one element may hold, which is what makes
    `'\\x1ff'` an error in a narrow char constant and not in a `U'...'` one.
    IT APPLIES TO `\\x` AND OCTAL AND TO NOTHING ELSE, because those two are
    the only escapes C defines as a VALUE the element has to hold: a `\\u`
    names a character, and `"\\u0100"` in a narrow string is a perfectly good
    two-byte one.

    Values are CODE POINTS here; turning them into bytes is `encode` below.
    `literal`, when given, receives one flag per value, True for the ones that
    came from a `\\x` or octal escape -- which is the one distinction `encode`
    cannot make afterwards and has to have: `"\\xe9"` is ONE byte and `"\\u00e9"`
    is the two that UTF-8 spells that character with, and by the time the
    values are numbers they look the same.
    """
    out: list[int] = []
    flags = literal if literal is not None else []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch != "\\":
            out.append(ord(ch))
            flags.append(False)
            i += 1
            continue
        i += 1
        if i >= n:
            raise LiteralError("escape sequence at end of literal", i)
        e = body[i]
        if e in _SIMPLE_ESCAPES and e not in "01234567":
            out.append(_SIMPLE_ESCAPES[e])
            flags.append(True)
            i += 1
            _fits(out[-1], max_value, i)
        elif e in "01234567":
            # AT MOST THREE OCTAL DIGITS, and the limit is why `'\\0777'` is two
            # characters rather than one out-of-range one. A greedy scan reads
            # `\\08` as an error instead of a NUL followed by an `8`.
            digits = ""
            while i < n and len(digits) < 3 and body[i] in "01234567":
                digits += body[i]
                i += 1
            out.append(int(digits, 8))
            flags.append(True)
            _fits(out[-1], max_value, i)
        elif e == "x":
            i += 1
            start = i
            # NO LIMIT ON HEX DIGITS -- this is the standard's rule and it is
            # the reason `"\\x41" "1"` is `A1` while `"\\x411"` is out of range
            # for a narrow string. A three-digit cap would silently change the
            # second into `A1` and no diagnostic would ever appear.
            while i < n and body[i] in _HEX:
                i += 1
            if i == start:
                raise LiteralError("\\x needs at least one hex digit", start)
            out.append(int(body[start:i], 16))
            flags.append(True)
            _fits(out[-1], max_value, i)
        elif e in "uU":
            want = 4 if e == "u" else 8
            i += 1
            digits = body[i:i + want]
            if len(digits) != want or any(c not in _HEX for c in digits):
                raise LiteralError(
                    f"\\{e} needs exactly {want} hex digits", i)
            value = int(digits, 16)
            # A UCN NAMES A CHARACTER, so what it has to fit in is Unicode
            # and not the element: `encode` spells it in the execution
            # encoding, which for a narrow string is UTF-8 and may be four
            # bytes. A surrogate is not a character and C says so.
            if value > 0x10FFFF or 0xD800 <= value <= 0xDFFF:
                raise LiteralError(
                    f"\\{e}{digits} is not a character", i)
            out.append(value)
            flags.append(False)
            i += want
        else:
            raise LiteralError(f"unknown escape sequence \\{e}", i - 1)
    return out


def _fits(value: int, max_value: int, at: int) -> None:
    if value > max_value:
        raise LiteralError(
            f"escape sequence value {value:#x} does not fit in "
            f"{max_value.bit_length()} bits", at)


# ── character constants ─────────────────────────────────────────────────────
#: Encoding prefix -> (element width in bytes, signed, name).
#:
#: `L` IS FOUR BYTES, which is a target decision and not a free one: it is what
#: every Unix-like platform uses and what `objects/` already assumes. A Windows
#: target wants two, and the day this frontend grows a `--target`-driven answer
#: this table is the one place it changes.
PREFIXES: dict[str, tuple[int, bool, str]] = {
    "": (1, True, "char"),
    "u8": (1, False, "char8_t"),
    "L": (4, True, "wchar_t"),
    "u": (2, False, "char16_t"),
    "U": (4, False, "char32_t"),
}


@dataclass(frozen=True, slots=True)
class CharConst:
    value: int
    prefix: str
    #: True when the constant held more than one character -- `'ab'`, which is
    #: legal, implementation-defined, and int-typed.
    multi: bool


def split_prefix(text: str, quote: str) -> tuple[str, str]:
    """`u8"hi"` -> (`u8`, `hi`). The quotes come off with the prefix."""
    end = text.rindex(quote)
    start = text.index(quote)
    return text[:start], text[start + 1:end]


def decode_char(text: str) -> CharConst:
    """A character constant's value and prefix. `text` includes its quotes."""
    prefix, body = split_prefix(text, "'")
    width, signed, _ = PREFIXES[prefix]
    bits = width * 8
    literal: list[bool] = []
    values = decode_escapes(body, max_value=(1 << bits) - 1 if prefix else 0xFF,
                            literal=literal)
    if not values:
        raise LiteralError("empty character constant")
    if prefix == "":
        # A NARROW CHARACTER CONSTANT IS ITS BYTES IN THE EXECUTION SET, and
        # that set is UTF-8: `'é'` is the two bytes 0xC3 0xA9 taken as a
        # multi-character constant, which is what gcc answers and what a
        # program comparing against `"é"[0]` needs. `'\\xe9'` is still the one
        # byte it was written as. C calls the value implementation-defined,
        # so the choice is this one's to make and it is made once, here.
        data = encode(values, "", literal)
        if len(data) == 1:
            v = data[0]
            # PLAIN CHAR IS SIGNED HERE, so `'\\xff'` is -1 and `'\\xff' < 0`
            # is true -- as it is on x86 Linux with gcc, and as it is not on
            # ARM. The choice is the target's; this frontend makes it once.
            return CharConst(v - 0x100 if v > 0x7F else v, prefix, multi=False)
        acc = 0
        for b in data:
            acc = ((acc << 8) | b) & 0xFFFFFFFF
        if acc >= 1 << 31:
            acc -= 1 << 32
        return CharConst(acc, prefix, multi=True)
    if len(values) == 1:
        v = values[0]
        if prefix == "":
            # PLAIN CHAR IS SIGNED HERE, so `'\\xff'` is -1 and `'\\xff' < 0` is
            # true -- as it is on x86 Linux with gcc, and as it is not on ARM.
            # The choice is the target's; this frontend makes it once, here.
            if v > 0x7F:
                v -= 0x100
        elif signed and v >= (1 << (bits - 1)):
            v -= 1 << bits
        return CharConst(v, prefix, multi=False)
    if prefix != "":
        raise LiteralError(
            f"{prefix}'...' holds one character, not {len(values)}")
    # `'abcd'` is int-valued and implementation-defined; this is the order gcc
    # and clang use, and a program relying on it is already unportable.
    acc = 0
    for v in values:
        acc = ((acc << 8) | (v & 0xFF)) & 0xFFFFFFFF
    if acc >= 1 << 31:
        acc -= 1 << 32
    return CharConst(acc, prefix, multi=True)


# ── string literals ─────────────────────────────────────────────────────────
def encode(values: list[int], prefix: str,
           literal: list[bool] | None = None) -> bytes:
    """Code points to the bytes a string literal occupies.

    Plain and `u8` strings are UTF-8, which is the execution character set
    here -- so `"\\u00e9"` and a literal `é` in the source are the same two
    bytes, and both differ from `"\\xe9"`, which is one. The wide forms are
    the target's native order, little-endian here.

    `literal` IS THE ONE THING THIS CANNOT WORK OUT FOR ITSELF. A HEX OR
    OCTAL ESCAPE IS A BYTE and a character is a character, and by the time
    both are numbers they look identical: 0xE9 is `\\xe9` or `é` depending on
    how it was written. `decode_escapes` knows and says so, and without the
    flags this falls back to treating every value as a character -- which is
    right for the `u8` case and for anything a preprocessor asks about.
    """
    width, _, _ = PREFIXES[prefix]
    if prefix in ("", "u8"):
        out = bytearray()
        for i, v in enumerate(values):
            if literal is not None and i < len(literal) and literal[i]:
                out.append(v & 0xFF)
            else:
                out.extend(chr(v).encode("utf-8"))
        return bytes(out)
    order = "little"
    if prefix == "u":
        out = bytearray()
        for v in values:
            if v > 0xFFFF:
                v -= 0x10000
                out += ((0xD800 | (v >> 10)).to_bytes(2, order))
                out += ((0xDC00 | (v & 0x3FF)).to_bytes(2, order))
            else:
                out += v.to_bytes(2, order)
        return bytes(out)
    return b"".join(v.to_bytes(width, order) for v in values)


def decode_string(text: str, literal: list[bool] | None = None
                  ) -> tuple[str, list[int]]:
    """A string literal's prefix and code points, escapes resolved."""
    prefix, body = split_prefix(text, '"')
    width, _, _ = PREFIXES[prefix]
    return prefix, decode_escapes(body, max_value=(1 << (width * 8)) - 1,
                                  literal=literal)


def join_strings(texts: list[str], literal: list[bool] | None = None
                 ) -> tuple[str, list[int]]:
    """Phase 6: adjacent string literals are one string.

    THE PREFIX OF THE WHOLE is decided before any escape is decoded, which is
    why this exists rather than a concatenation of decoded pieces. `"\\xff"
    L"a"` is a WIDE string whose first element is 0xFF, not a narrow byte
    followed by a wide one -- and `u"a" U"b"` is a constraint violation
    rather than a silent widening.
    """
    prefixes = {split_prefix(t, '"')[0] for t in texts}
    wide = {p for p in prefixes if p not in ("", "u8")}
    if len(wide) > 1:
        raise LiteralError(
            "concatenating string literals with different encoding prefixes: "
            + ", ".join(sorted(f"{p}\"\"" for p in sorted(wide))))
    if wide:
        prefix = wide.pop()
    elif "u8" in prefixes:
        prefix = "u8"
    else:
        prefix = ""
    width, _, _ = PREFIXES[prefix]
    values: list[int] = []
    for t in texts:
        _, body = split_prefix(t, '"')
        values.extend(decode_escapes(body, max_value=(1 << (width * 8)) - 1,
                                     literal=literal))
    return prefix, values


# ── numeric constants ───────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class IntConst:
    value: int
    #: How many bits the type the constant GETS occupies, and whether it is
    #: signed. See the module docstring on why the type is part of the answer.
    bits: int
    signed: bool
    #: `long long` and `long` are both 64 bits here, and a diagnostic that
    #: says "long" when the source said "long long" reads as a compiler bug.
    spelling: str
    #: A `wb` or `uwb` suffix: the type is `_BitInt(bits)` rather than the
    #: standard type of that width, and `bits` is the SMALLEST that can
    #: represent the value -- with a bit for the sign when it has one.
    bitint: bool = False


@dataclass(frozen=True, slots=True)
class FloatConst:
    value: float
    #: 4 for an `f` suffix, 16 for an `l` one, 8 otherwise -- the SIZE of
    #: the type the constant gets, which is how the parser tells the three
    #: apart. 16 is `long double`, and see `exact` for why it needs one.
    size: int
    spelling: str
    #: An `i` or `j` suffix: `1.0i` is `0 + 1i`, a complex constant. NOT in
    #: the standard -- C has no imaginary constants at all and `<complex.h>`
    #: writes `i` as `_Complex_I` -- but gcc has had this for thirty years,
    #: glibc's own `<complex.h>` defines `_Complex_I` as `1.0iF`, and a
    #: header that does that is the reason to support it.
    imaginary: bool = False
    #: THE VALUE AGAIN, EXACTLY, as a `Fraction`. `value` is a Python float
    #: and has 53 bits of significand; a `long double` has 64, so a constant
    #: of that type would lose eleven bits before it reached the program.
    #: Only filled in for an `l` suffix, because only that type needs it.
    exact: object = None


def _strip_separators(text: str) -> str:
    """C23 digit separators. `1'000'000` is one million, not a syntax error."""
    return text.replace("'", "")


def is_float_spelling(text: str) -> bool:
    """True if this pp-number is a floating constant rather than an integer.

    A `.` decides it, and so does an exponent -- but `e` is a hex DIGIT, so
    `0xE1` is an integer and only a `p` makes a hex constant floating. Getting
    this backwards makes `0xE5` a float, which then fails to be a case label.
    """
    t = _strip_separators(text).lower()
    # AN IMAGINARY SUFFIX MAKES IT FLOATING whatever the digits look like.
    # `1i` is `0 + 1i` here, not gcc's `_Complex int` -- an integer complex
    # is a GNU extension of a GNU extension, and every use of the spelling
    # in a real header means the floating one.
    body = t
    tail = ""
    while body and body[-1] in "flij":
        tail = body[-1] + tail
        body = body[:-1]
    if "i" in tail or "j" in tail:
        return True
    if t.startswith("0x"):
        return "." in t or "p" in t
    if t.startswith("0b"):
        return False
    return "." in t or "e" in t


def parse_number(text: str) -> IntConst | FloatConst:
    """Decode a pp-number. Raises LiteralError if it is not a constant at all.

    This is where `1.2.3` finally becomes an error: it survived phase 3 as one
    pp-number because the preprocessor is not allowed to care, and it reaches
    here only if it was not deleted by a conditional and not pasted into
    something else.
    """
    raw = _strip_separators(text)
    return _float(raw, text) if is_float_spelling(raw) else _integer(raw, text)


def _float(raw: str, spelling: str) -> FloatConst:
    body, size = raw, 8
    suffix = ""
    while body and body[-1] in "fFlLiIjJ":
        suffix = body[-1] + suffix
        body = body[:-1]
    imaginary = any(c in "iIjJ" for c in suffix)
    # `1.0iF` AND `1.0fi` ARE THE SAME CONSTANT: gcc accepts the imaginary
    # letter on either side of the size letter, and glibc's `<complex.h>`
    # uses the first spelling.
    letters = "".join(c for c in suffix if c not in "iIjJ")
    if letters.lower() == "f":
        size = 4
    elif letters.lower() == "l":
        size = 16
    elif letters.lower() not in ("", "l"):
        raise LiteralError(f"invalid suffix {suffix!r} on floating constant")
    if len(letters) > 1 or len(suffix) - len(letters) > 1:
        raise LiteralError(f"invalid suffix {suffix!r} on floating constant")
    try:
        if body.lower().startswith("0x"):
            # Python's float.fromhex wants `0x1.8p3` and accepts exactly the
            # C spelling, including a missing exponent -- which C requires and
            # Python does not, so that case is checked here.
            if "p" not in body.lower():
                raise LiteralError(
                    "hexadecimal floating constant needs a `p` exponent")
            value = float.fromhex(body)
        else:
            value = float(body)
    except LiteralError:
        raise
    except ValueError:
        raise LiteralError(f"{spelling!r} is not a valid floating constant") from None
    exact = None
    if size == 16:
        from .ldouble import from_decimal, round_to
        try:
            exact = round_to(from_decimal(body))
        except (ValueError, ArithmeticError):
            exact = None
    return FloatConst(value, size, spelling, imaginary, exact)


def _integer(raw: str, spelling: str) -> IntConst:
    body = raw
    suffix = ""
    while body and body[-1] in "uUlLzZwWbB":
        suffix = body[-1] + suffix
        body = body[:-1]
    low = suffix.lower()
    if low not in ("", "u", "l", "ul", "lu", "ll", "ull", "llu", "z", "uz",
                   "zu", "wb", "uwb", "wbu"):
        raise LiteralError(f"invalid suffix {suffix!r} on integer constant")
    # A suffix is `ll` or `LL`, never `lL`: the standard spells the two out
    # separately, and mixing them is how a typo in a header becomes a value of
    # the wrong type rather than an error.
    if "ll" in low and suffix.lower() != suffix and suffix.upper() != suffix:
        raise LiteralError(f"invalid suffix {suffix!r} on integer constant")
    try:
        if body.lower().startswith("0x"):
            value, base = int(body, 16), 16
        elif body.lower().startswith("0b"):
            value, base = int(body, 2), 2
        elif body.startswith("0") and body != "0":
            value, base = int(body, 8), 8
        else:
            value, base = int(body, 10), 10
    except ValueError:
        raise LiteralError(f"{spelling!r} is not a valid integer constant") from None

    unsigned = "u" in low
    long_ = "l" in low or "z" in low
    llong = "ll" in low
    if "wb" in low:
        # C23'S OWN SUFFIX, and the width is not chosen from a candidate list
        # the way every other integer constant's is: it is the SMALLEST that
        # represents the value. `42wb` is `_BitInt(7)` -- six bits for the
        # value and one for the sign it is allowed to have -- and `42uwb` is
        # `unsigned _BitInt(6)`.
        want = max(1, value.bit_length()) if unsigned \
            else max(2, value.bit_length() + 1)
        if want > 64:
            raise LiteralError(
                f"{spelling!r} needs a `_BitInt({want})`, which is wider "
                f"than this implementation's 64")
        return IntConst(value, want, not unsigned, spelling=spelling,
                        bitint=True)
    return IntConst(value, *_int_type(value, base, unsigned, long_, llong),
                    spelling=spelling)


def _int_type(value: int, base: int, unsigned: bool, long_: bool,
              llong: bool) -> tuple[int, bool]:
    """The (bits, signed) a constant gets. See the module docstring.

    `int` is 32 bits and `long` is 64; the candidate list below is that target
    written out. A decimal constant never becomes unsigned on its own, which is
    the rule that makes `4294967295` a `long` and `0xFFFFFFFF` an `unsigned
    int`.
    """
    decimal = base == 10
    if llong or long_:
        if unsigned:
            candidates = [(64, False)]
        elif decimal:
            candidates = [(64, True)]
        else:
            candidates = [(64, True), (64, False)]
    elif unsigned:
        candidates = [(32, False), (64, False)]
    elif decimal:
        candidates = [(32, True), (64, True)]
    else:
        candidates = [(32, True), (32, False), (64, True), (64, False)]
    for bits, signed in candidates:
        limit = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1
        if value <= limit:
            return bits, signed
    # Wider than anything; C says the behaviour is undefined and every
    # compiler warns and truncates. Truncating silently is the one option
    # that cannot be debugged, so the caller reports and this returns the
    # widest type rather than lying about the value fitting.
    raise LiteralError(
        f"integer constant {value} is too large for any integer type",
        code="E1011")

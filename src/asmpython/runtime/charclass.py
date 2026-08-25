# WHAT A CHARACTER IS, over the generated table next door.
#
# HAND-WRITTEN, and separate from `unicode_table.py` for that reason: that
# file says GENERATED at the top and means it, so anything living in it is one
# `tools/gen_unicode_ir.py` away from being silently deleted. The table is
# data and this is the reading of it.
#
# THE MASKS ARE THE C'S `APY_UC_*`, checked against a real compiler by the
# offsetof probe rather than read off the header by eye.
#
# ASCII IS WRITTEN OUT AND THE REST IS TABLED, which is the same split the C
# makes and the reason the generated table starts at U+0085: every string is
# mostly ASCII, and a binary search per character to learn that `a` is a
# letter would be a thousand comparisons a repr does not need.


# THE CLASS BITS, filled from the C by the offsetof probe.
def apy_uc_alpha() -> i64:
    return 1


def apy_uc_decimal() -> i64:
    return 2


def apy_uc_digit() -> i64:
    return 4


def apy_uc_numeric() -> i64:
    return 8


def apy_uc_lower() -> i64:
    return 16


def apy_uc_upper() -> i64:
    return 32


def apy_uc_title() -> i64:
    return 64


def apy_uc_space() -> i64:
    return 128


def apy_uc_printable() -> i64:
    return 256


def apy_uc_xidstart() -> i64:
    return 512


def apy_uc_xidcont() -> i64:
    return 1024


def apy_char_class_of(cp: i64) -> i64:
    """Which classes one code point belongs to.

    ASCII IS WRITTEN OUT rather than tabled, because it is a handful of
    ranges and every string is mostly made of it -- the table starts at
    U+0085 for exactly that reason.
    """
    if cp >= 128:
        return apy_uc_lookup(cp)
    m: i64 = 0
    if (cp >= 65 and cp <= 90) or (cp >= 97 and cp <= 122):
        m = m | apy_uc_alpha() | apy_uc_xidstart() | apy_uc_xidcont()
    if cp >= 48 and cp <= 57:
        m = m | apy_uc_decimal() | apy_uc_digit() | apy_uc_numeric() | apy_uc_xidcont()
    if cp >= 97 and cp <= 122:
        m = m | apy_uc_lower()
    if cp >= 65 and cp <= 90:
        m = m | apy_uc_upper()
    if cp == 32 or cp == 9 or cp == 10 or cp == 11 or cp == 12 or cp == 13:
        m = m | apy_uc_space()
    if cp >= 32 and cp < 127:
        m = m | apy_uc_printable()
    if cp == 95:
        m = m | apy_uc_xidstart() | apy_uc_xidcont()
    return m


def apy_cp_printable_of(cp: i64) -> i64:
    """Does this character show as itself in a repr?"""
    if apy_char_class_of(cp) & apy_uc_printable():
        return 1
    return 0

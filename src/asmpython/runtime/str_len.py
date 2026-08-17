# Length and truth for strings, written in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md, the second str step. `str_cell.py` builds
# a cell; this file is the first ported code that READS one, and it is the
# shape every remaining kind takes: a fast path over a shrinking C remainder,
# declining anything it was not told about.
#
# THE LAYOUT HELPERS COME FROM `str_cell.py` and `int_cell.py`. The runtime
# compiles as ONE unit, so its files call each other by name.
#
# WHY THESE THREE AND NOT THE STRING LIBRARY. `apy_raw_len`, `apy_len` and
# `apy_truth` are polymorphic over every kind, and their str/bytes arms are
# the whole of what a string contributes: one field read, or one walk. The
# case transforms and the search family are a different problem -- they
# allocate, or they need the unicode table -- and porting a leaf that nobody
# reaches is how a port looks busy without moving.


def apy_str_char_count(v: ptr) -> i64:
    """The CHARACTER count of a str cell, which is not its byte length.

    A cell stores UTF-8 bytes and Python indexes characters, and nothing
    caches the difference -- there is no room in the cell for a count and
    adding one would change `sizeof(apy_obj)` under fifteen thousand lines of
    C that were not told. So every `len()` walks, exactly as the C's own
    `apy_str_chars` does.

    A CONTINUATION BYTE IS `10xxxxxx` and every other byte begins a character,
    so the count is the number of bytes that are not continuations. That is
    the whole of UTF-8 that this needs to know: no decoding, no validation,
    no table.

    AN IR-ONLY HELPER. It does not replace the C's `apy_str_chars`, which is
    `static` and therefore invisible to the subset -- the two coexist and must
    agree, which is what the differential suite is for.
    """
    n: i64 = load(i64, offset(v, apy_str_len_offset()))
    p: ptr = ptr(load(u64, offset(v, apy_str_ptr_offset())))
    chars: i64 = 0
    i: i64 = 0
    while i < n:
        if (load(u8, offset(p, i)) & u8(192)) != u8(128):
            chars = chars + 1
        i = i + 1
    return chars


def apy_is_str(v: ptr) -> bool:
    return load(i32, offset(v, apy_kind_offset())) == i32(apy_str_kind())


def apy_is_bytes(v: ptr) -> bool:
    return load(i32, offset(v, apy_kind_offset())) == i32(apy_bytes_kind())


def apy_str_byte_len(v: ptr) -> i64:
    return load(i64, offset(v, apy_str_len_offset()))


def apy_raw_len(v: ptr) -> i64:
    """`len()` as a machine word, for the frontend's own loop bounds.

    BYTES COUNTS OCTETS AND STR COUNTS CHARACTERS, and the two must not be
    confused here of all places: a `for` loop takes its bound from this and
    its elements from the subscript, so a string answering its byte length
    walks off the end of itself the moment it holds a non-ASCII character.
    The C's own comment records that as a bug it already had.
    """
    if apy_is_bytes(v):
        return apy_str_byte_len(v)
    if apy_is_str(v):
        return apy_str_char_count(v)
    return apy_raw_len_slow(v)


def apy_len(v: ptr) -> ptr:
    """`len()` as an object. The same two arms, boxed."""
    if apy_is_bytes(v):
        return apy_from_int(apy_str_byte_len(v))
    if apy_is_str(v):
        return apy_from_int(apy_str_char_count(v))
    return apy_len_slow(v)


def apy_truth(v: ptr) -> i64:
    """Emptiness, for the kinds that HAVE an emptiness.

    THE BYTE LENGTH AND NOT THE CHARACTER COUNT, which is not a shortcut: a
    string is empty exactly when it has no bytes, and the two counts are zero
    together. Walking it to decide would be the same answer more slowly.

    EVERYTHING ELSE DECLINES, and that is the load-bearing part. `apy_truth`
    is polymorphic over every kind and its default is TRUE -- a function, a
    type, an iterator. A fast path that read `v.s.n` for those would read
    whatever field the union overlaps, which for a type is its base pointer;
    the C carries a comment about exactly that bug, where `if et:` on a caught
    exception's type answered False for every class with no base.
    """
    if apy_is_str(v) or apy_is_bytes(v):
        if apy_str_byte_len(v) != 0:
            return 1
        return 0
    return apy_truth_slow(v)

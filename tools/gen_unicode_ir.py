import pathlib
import re
import struct

SRC = pathlib.Path("src/asmpython/objects/c/unicode_table.py")
rows = re.findall(r"\{(\d+),(\d+),(\d+)\}", SRC.read_text(encoding="utf-8"))
rows = [(int(a), int(b), int(c)) for a, b, c in rows]
assert rows and rows == sorted(rows), "the C table is not sorted by `lo`"

blob = b"".join(struct.pack("<III", lo, hi, m) for lo, hi, m in rows)

HEAD = '''# THE UNICODE CHARACTER CLASSES, in the machine subset. GENERATED.
#
# Written by `tools/gen_unicode_ir.py` from `objects/c/unicode_table.py`,
# which is itself generated from the reference implementation's own data.
# Do not edit: `test_unicode_table_agrees` compares the two and fails if they
# drift.
#
# ONE BLOB AND A BINARY SEARCH, rather than a switch of {n} ranges. The C
# stores `struct {{ unsigned lo, hi, m; }}` and this stores the same twelve
# bytes per row, little-endian, sorted by `lo` -- so the search is the same
# search and the table is the same table.
#
# WHY THIS HAD TO MOVE AT ALL: "is this character printable" is a question
# about the CHARACTER and not the byte. U+00A0 and U+2003 are spaces Python
# escapes, and asked a byte at a time neither one is anything. Every string
# repr needs this, which is why `apy_text_of` could not answer one without it.


def apy_uc_rows() -> i64:
    """How many ranges the table holds."""
    return {n}


def apy_uc_stride() -> i64:
    """Bytes per row: three unsigned 32-bit fields."""
    return 12


def apy_uc_table() -> ptr:
    """The ranges, as `lo`, `hi`, `mask` triples sorted by `lo`."""
    return rodata({blob!r})


def apy_uc_lookup(cp: i64) -> i64:
    """The character-class mask for one code point, or 0.

    BINARY SEARCH, because the table is sorted and has {n} rows -- a linear
    walk would be a thousand comparisons per character on a repr.

    ASCII IS ANSWERED BY THE CALLER. Everything under 0x80 has its classes
    written out in `apy_char_class`, so the table starts at U+0085 and this
    returns 0 rather than searching for something that is not there.
    """
    if cp < 128:
        return 0
    table: ptr = apy_uc_table()
    lo: i64 = 0
    hi: i64 = apy_uc_rows() - 1
    while lo <= hi:
        mid: i64 = (lo + hi) // 2
        row: ptr = offset(table, mid * apy_uc_stride())
        first: i64 = i64(load(u32, row))
        last: i64 = i64(load(u32, offset(row, 4)))
        if cp < first:
            hi = mid - 1
        elif cp > last:
            lo = mid + 1
        else:
            return i64(load(u32, offset(row, 8)))
    return 0
'''

out = pathlib.Path("src/asmpython/runtime/unicode_table.py")
out.write_text(HEAD.format(n=len(rows), blob=blob), encoding="utf-8")
print(f"{len(rows)} rows, {len(blob)} bytes, {out.stat().st_size} on disk")

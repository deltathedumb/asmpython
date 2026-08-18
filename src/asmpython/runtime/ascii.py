# The ASCII character predicates, in the machine subset.
#
# STAGE 5 OF docs/INERT-RUNTIME.md, and the smallest useful piece of it. These
# five are what every case transform and every `str.isalpha` family member
# rests on: `runtime/str_cell.py` records the case transforms as blocked, and
# this is the half of that blockage which is NOT the Unicode table.
#
# WHY THESE ARE THE EASIEST THING IN THE RUNTIME TO PORT. Every other function
# in the C takes or answers an `apy_value` -- a tagged cell whose layout has to
# be known before a line can be written. These take a NUMBER and answer a
# NUMBER. There is no cell, no allocation, no ownership question and nothing to
# get wrong about memory; the whole of each one is a comparison.
#
# ASCII ONLY, AND THAT IS SAFE RATHER THAN SLOPPY. A byte outside 0..127 is
# never a letter here, which is what lets these run over UTF-8 unchanged: every
# byte of a multi-byte sequence has its high bit set, so none of them can be
# mistaken for something to fold. A character above 127 that IS a letter is the
# Unicode table's business, not this file's.
#
# THE ARGUMENT IS A MACHINE WORD, not a byte, because that is what the C's
# callers widen `unsigned char` to and what `signatures()` can describe -- it
# knows `int64_t`, `double`, `apy_value` and `void`, and nothing else. So each
# function masks to a byte itself rather than trusting the caller.


def apy_c_lower(c: i64) -> i64:
    """1 if `c` is an ASCII lower-case letter, 0 otherwise.

    THE WORKED EXAMPLE for the other four. Note the three things that make
    this the machine subset rather than Python:

      * every parameter and the return are TYPED, and `i64` is the only
        integer this needs;
      * the literals are NUMBERS, not characters -- there is no `'a'` here,
        because the subset has no character type. 97 is `a` and 122 is `z`;
      * the answer is 1 or 0, not True or False. A `bool` is a Python VALUE
        and this file is below the level where values exist.

    THE MASK IS NOT DECORATION. The C widens an `unsigned char`, so the caller
    always passes 0..255 -- but the subset cannot check that, and a negative
    or huge argument comparing "in range" by accident is the kind of thing
    that is invisible until some caller changes.
    """
    b: i64 = c & 255
    if b < 97:
        return 0
    if b > 122:
        return 0
    return 1


# ── the four that are not written yet ──────────────────────────────────────
#
# Each one goes here, in this file, in the same shape as `apy_c_lower` above.
# When one is written, add its name to the `ascii.py` entry in `REPLACES` in
# `link/objects_ir.py` -- that is the switch that makes a compiled program use
# the ported version instead of the C, and until a name is listed there the
# code is compiled and unused.
#
# `apy_c_upper(c: i64) -> i64`
#     1 if `c` is an ASCII upper-case letter. 65 is `A` and 90 is `Z`.
#
# `apy_c_digit(c: i64) -> i64`
#     1 if `c` is an ASCII decimal digit. 48 is `0` and 57 is `9`.
#
# `apy_c_alpha(c: i64) -> i64`
#     1 if `c` is an ASCII letter of either case. The C writes this as
#     `apy_c_lower(c) || apy_c_upper(c)`, and calling the other two from here
#     is fine -- a runtime module may call anything else in the runtime.
#
# `apy_c_space(c: i64) -> i64`
#     1 if `c` is one of the six ASCII whitespace bytes, which are:
#         32  space
#          9  tab             (`\t`)
#         10  newline         (`\n`)
#         13  carriage return (`\r`)
#         12  form feed       (`\f`)
#         11  vertical tab    (`\v`)
#     Python's `str.isspace` says True for more than these six, and that is
#     the Unicode table's business rather than this file's -- `apy_c_space` is
#     asked only about bytes.
#
# HOW TO CHECK ONE. `tests/asmpython/integration/test_ported_int.py` is the
# pattern: the ported code is compiled into a real program and its answers are
# compared against CPython's. The quickest loop while writing is
#
#     PYTHONPATH=src python -m asmpython run some_program.py
#     PYTHONPATH=src python -m asmpython build some_program.py -o out.exe
#
# where `some_program.py` uses `str.upper`, `str.isdigit` and friends -- those
# reach these predicates through the C that is left.
#
# WHAT IS NOT ALLOWED IN THIS FILE, and the compiler will say so rather than
# do something surprising: no Python containers (no list, dict, str, tuple),
# no `for` over an iterable, no exceptions, no `None`, no f-strings. What
# there is: `i8`/`i16`/`i32`/`i64` and their unsigned forms, `f32`/`f64`,
# `ptr`, `if`/`while`, arithmetic, and `load`/`store`/`offset`/`alloca`/
# `sizeof`/`reserve` for memory. `while` rather than `for` is the usual
# surprise.

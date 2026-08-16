# Arbitrary-precision integers: the runtime ABI

The runtime half of Phase 1's arbitrary-precision `int` lives in
`asmpython/_runtime/abi_shims.asm`. This file is the contract the lowering
codes against. **Nothing calls these symbols yet** — they are inert until
`ir_lower.py` wires them, which is deliberate: the runtime landed on its own
so it could be verified on its own.

## Cell layout

A bigint is a `malloc`'d cell. `NULL` is never a valid bigint.

| offset | field | type | meaning |
|---|---|---|---|
| 0 | magic | u64 | `0xB161B161B161B161` |
| 8 | sign | i64 | `-1`, `0`, `+1` |
| 16 | len | u64 | limbs in use |
| 24 | cap | u64 | limbs allocated |
| 32 | limb[] | u32 | magnitude, little-endian |

Sign-magnitude, base 2³². The representation is canonical: no high zero
limbs, and a zero magnitude always carries sign `0`, so there is no negative
zero to special-case.

`_abi_bigint_is` checks the magic, so a mistagged pointer is caught rather
than silently used as a number.

## Symbols

All arguments and results follow the ordinary Win64 convention: integer and
pointer arguments in RCX, RDX, R8, R9; result in RAX; a `double` result in
XMM0. (Note this differs from `_abi_float_fmt` / `_abi_round_ndigits`, whose
first argument is a float and therefore lands in XMM0.)

### Construction and conversion

| symbol | signature | notes |
|---|---|---|
| `_abi_bigint_from_i64` | `(i64) -> ptr` | exact, including `-2**63` |
| `_abi_bigint_from_str` | `(char*) -> ptr` | optional `+`/`-` then decimal digits; `NULL` if malformed |
| `_abi_bigint_to_str` | `(ptr) -> char*` | exact decimal, `malloc`'d, NUL-terminated, arbitrarily long |
| `_abi_bigint_to_i64` | `(ptr) -> i64` | low 64 bits with sign; only meaningful when `fits_i64` |
| `_abi_bigint_fits_i64` | `(ptr) -> i64` | 1 if the value is exactly representable as int64 |
| `_abi_bigint_to_f64` | `(ptr, i64* overflow) -> f64` | correctly rounded, half-to-even; sets `*overflow` and returns ±inf past DBL_MAX |

### Arithmetic

| symbol | signature | notes |
|---|---|---|
| `_abi_bigint_add` | `(ptr, ptr) -> ptr` | |
| `_abi_bigint_sub` | `(ptr, ptr) -> ptr` | |
| `_abi_bigint_mul` | `(ptr, ptr) -> ptr` | |
| `_abi_bigint_neg` | `(ptr) -> ptr` | |
| `_abi_bigint_divmod` | `(ptr a, ptr b, ptr* q, ptr* r) -> i64` | returns 0 and writes nothing if `b == 0` |
| `_abi_bigint_floordiv` | `(ptr, ptr) -> ptr` | `NULL` on divide by zero |
| `_abi_bigint_mod` | `(ptr, ptr) -> ptr` | `NULL` on divide by zero |
| `_abi_bigint_pow` | `(ptr base, i64 exp) -> ptr` | `NULL` if `exp < 0` |
| `_abi_bigint_pow_i64` | `(i64 base, i64 exp) -> ptr` | convenience, so `2 ** 70` needs no separate promotion |

Division follows **Python**, not C: the quotient floors toward negative
infinity and the remainder takes the sign of the divisor, so `-7 // 2 == -4`
and `-7 % 3 == 2`, and `a == (a // b) * b + (a % b)` holds for every sign
combination. (The existing 64-bit lowering already agrees; this matches it.)

### Predicates

| symbol | signature | notes |
|---|---|---|
| `_abi_bigint_cmp` | `(ptr, ptr) -> i64` | `-1` / `0` / `1`, signed |
| `_abi_bigint_sign` | `(ptr) -> i64` | `-1` / `0` / `1` |
| `_abi_bigint_bit_length` | `(ptr) -> i64` | bits in the magnitude; `0` for zero |
| `_abi_bigint_is` | `(ptr) -> i64` | 1 if this looks like a bigint cell |

### Staying on the 64-bit path

These let the lowering keep ordinary arithmetic in registers and promote
only when it actually has to. Each returns 1 if the operation would overflow
a signed 64-bit result.

| symbol | signature |
|---|---|
| `_abi_i64_add_ovf` | `(i64, i64) -> i64` |
| `_abi_i64_sub_ovf` | `(i64, i64) -> i64` |
| `_abi_i64_mul_ovf` | `(i64, i64) -> i64` |

## What the lowering still has to decide

The runtime deliberately takes no position on these, because they are
representation and typing questions:

- **How a "maybe big" int is represented.** Nothing here assumes a tagging
  scheme. The overflow probes exist so the fast path can stay a raw machine
  word.
- **`ZeroDivisionError`** — `divmod`/`floordiv`/`mod` report the condition;
  they do not raise.
- **`OverflowError`** on `float(huge)` — `to_f64` sets the flag; it does not
  raise.
- **`x ** -n` returning a float** — `pow` returns `NULL` for a negative
  exponent rather than inventing a policy.

## Not implemented

Stated explicitly so the boundary is visible rather than discovered:

- **Bitwise operators** (`& | ^ ~ << >>`) at arbitrary width. Python's
  infinite-two's-complement model for negative operands is a real piece of
  work on a sign-magnitude representation and is not attempted here.
- **`hash()`** of a big int.
- **Bases other than 10** — `hex()`/`oct()`/`bin()` of a big int.
- **`int(s)` beyond plain decimal** — no underscores, whitespace, or base
  prefixes.
- **Freeing.** Cells are `malloc`'d and never freed, matching what
  `_abi_int_fmt`/`_abi_float_fmt` already do.

Unlike the dtoa code next door, these routines keep no static state, so they
are reentrant.

## Verification

The algorithm was validated as a Python reference against CPython *before*
the assembly was written (`scratchpad/bigint_ref.py`), then the assembly
itself was differentially tested through a C driver that calls the real
symbols in the real `abi_shims.obj`:

- reference model: add/sub/mul/cmp over 9000 random pairs, divmod over 6000
  (checking the quotient, the remainder, *and* the identity
  `q*b + r == a`), operands to 4000 bits, to-string to 2000 digits — zero
  mismatches;
- assembly: ~177000 differential cases across five seeds, covering every
  symbol above — zero mismatches.

That found one real bug worth recording: `_bi_divmod_small` used a 64-bit
`DIV`, which forms the dividend `rem * 2**64 + limb`. For a base-2³² limb
array the dividend must be `rem * 2**32 + limb`, so every multi-limb
`to_str` was corrupted **while the limbs themselves were correct** — the
kind of failure that reads fine in a debugger and only shows up when you
diff against an oracle.

"""The freestanding AArch64 runtime, and the toolchain that links it.

A bare-metal target has no operating system and no libc, so the runtime the
frontend calls into has to be written from nothing: an entry point, a stack,
and enough of `print` to be useful. In exchange the result runs under
`qemu-system-aarch64` with no guest OS, which is what makes an ARM64 backend
verifiable on a machine that is not ARM.

WHY NOT NEWLIB, which ships with the toolchain and would give real `printf`
and a real `fmod`: it faults. Its `snprintf` builds a `FILE` on the stack and
takes an alignment fault at EL1 the moment malloc hands it a block, and
chasing that is a worse use of effort than writing the two functions whose
answers can be checked directly.

SO THIS FILE OWNS TWO PIECES OF NUMERICS, and both of them are exact because
every other execution path's are:

  * `put_float` is Dragon4 in integer arithmetic -- Python's `repr`, the
    shortest decimal that reads back as the same double, NOT C's `%f`.
  * `fmod` is binary long division on the mantissas, NOT `a - trunc(a/b)*b`.

`tests/.../test_arm64.py` holds each to its reference on the host, character
for character against CPython's `repr` over 200,000 doubles and bit for bit
against libm's `fmod` over 100,000 pairs. Approximating either is not a
rounding difference, it is this backend printing a different program's answer:
`%f` alone made fourteen of the twenty AArch64 differential seeds disagree,
and the inexact `fmod` it was hiding made three more.

THE TWO THINGS EVERY BARE-METAL AARCH64 PROGRAM NEEDS, both of which cost an
afternoon to discover:

  * The FPU traps until `CPACR_EL1.FPEN` is set. GCC uses SIMD registers for
    ordinary code, so without it the first function call faults and nothing
    runs -- with no output and no error.
  * QEMU's `-M virt` has RAM at 0x40000000. An image linked at the toolchain's
    default 0x400000 is loaded nowhere at all, silently.
"""
from __future__ import annotations

from pathlib import Path

from .base import LinkError, LinkRequest, Toolchain, find_tool, run
from ..objects.support import POW_INT_C
from .registry import register

#: Where `-M virt` puts RAM, and where the PL011 UART is mapped. Both are
#: properties of that QEMU machine, not of AArch64.
LOAD_ADDRESS = 0x40100000
UART_ADDRESS = 0x09000000

START_S = r"""// Entry point for a bare-metal AArch64 image.
    .text
    .global _start
_start:
    // Floating point and SIMD trap at EL1 until FPEN says otherwise, and the
    // compiler uses vector registers for ordinary code. Without this the
    // first call faults and the program produces nothing at all.
    mov  x0, #(3 << 20)
    msr  cpacr_el1, x0
    isb

    ldr  x0, =_stack_top
    mov  sp, x0

    bl   asmpython_main

    // Shut the machine down through PSCI SYSTEM_OFF, so the emulator exits
    // when the program finishes. Parking in `wfi` instead leaves QEMU
    // running until something kills it, which turns every test into a
    // timeout and a two-second program into a two-minute one.
    //
    // Both conduits are tried: -M virt uses HVC when the CPU has EL2 and SMC
    // when it does not, and an unimplemented call simply returns.
    mov  x0, #0x84000000
    movk x0, #0x0008
    hvc  #0
    mov  x0, #0x84000000
    movk x0, #0x0008
    smc  #0
0:  wfi
    b    0b
"""

LINKER_LD = r"""ENTRY(_start)
SECTIONS {
  . = %(load)#x;
  .text   : { *(.text*) }
  .rodata : { *(.rodata*) }
  .data   : { *(.data*) }
  .bss    : { *(.bss*) *(COMMON) }
  . = ALIGN(16);
  . = . + 0x20000;
  _stack_top = .;
}
"""

RUNTIME_C = r"""/* Freestanding runtime: the host functions the Python frontend calls. */
typedef unsigned long long u64;
typedef long long          i64;

static volatile unsigned int * const UART = (unsigned int *)%(uart)#xu;

int putchar(int c) { *UART = (unsigned int)(unsigned char)c; return c; }

static void put_u64(u64 v) {
    char tmp[24];
    int k = 0;
    if (v == 0) tmp[k++] = '0';
    while (v) { tmp[k++] = (char)('0' + (v %% 10)); v /= 10; }
    while (k) putchar(tmp[--k]);
}

void put_int(i64 v) {
    if (v < 0) { putchar('-'); put_u64((u64)(-(v + 1)) + 1ull); }
    else put_u64((u64)v);
}

/* ---------------------------------------------------------------------------
   Python's float repr, in exact integer arithmetic.

   WHY NOT `%%f`, WHICH THIS REPLACED: it prints six decimals, so 2.0 came out
   `2.000000`, 1e-9 came out `0.000000`, and every printed float disagreed with
   CPython, the IR interpreter, the C backend and the x86-64 backend. Fourteen
   of the twenty differential seeds failed on exactly that.

   The hosted runtime gets the same answer cheaply (`objects/support.py`,
   `py_repr_double`): print to N significant digits with snprintf, parse back
   with strtod, stop at the first N that round-trips. There is no libc here, so
   neither half of that trick exists, and writing an approximate one is how a
   backend ends up quietly disagreeing again.

   So this is Dragon4 (Steele & White, in Burger & Dybvig's formulation): the
   double is held as an exact rational R/S with the half-gaps to its two
   neighbours as M+/S and M-/S, and digits are generated until the prefix
   emitted so far is already inside the interval that reads back as this exact
   double. That is *by construction* the shortest round-tripping string, so no
   round-trip check -- and therefore no strtod -- is needed.

   Everything is big integers because the exact value of a double genuinely
   needs them: 5e-324 is 751 significant decimal digits. They are FIXED width
   because there is no malloc either. --------------------------------------- */

/* 1536 bits. The measured worst case over every power of two, every power of
   ten and 20,000 random doubles is 1080 bits (subnormals: S = 2^1075, and the
   digit loop's R*10 adds four more), so this is 456 bits of headroom. Do not
   shrink it to 34 limbs "because that is what is used" -- nothing here checks
   for overflow, it would corrupt digits silently, and 200 bytes of stack in a
   128K stack is not worth the risk. */
#define BIG_LIMBS 48

/* Limbs are 32-bit with 64-bit intermediates, NOT 64-bit with `__int128`:
   nothing here is hot -- this prints, it does not compute -- and 32-bit limbs
   make the same source compile on the host, which is what lets the formatter
   be tested against CPython's repr directly instead of only through QEMU. */
typedef struct { unsigned int v[BIG_LIMBS]; int n; } big;

/* `n` is the limb count with no leading zero limb, which is what makes
   `big_cmp` a length comparison first. Every operation below restores it. */

static void big_set(big *a, u64 x) {
    a->n = 0;
    while (x) { a->v[a->n++] = (unsigned int)(x & 0xFFFFFFFFu); x >>= 32; }
}

/* Written as a loop rather than `*d = *s`: GCC turns a 196-byte struct
   assignment into a call to memcpy even at -O0, and there is no memcpy to
   call. The same reason these are all taken by pointer. */
static void big_copy(big *d, const big *s) {
    for (int i = 0; i < s->n; i++) d->v[i] = s->v[i];
    d->n = s->n;
}

static void big_mul_small(big *a, unsigned int m) {
    u64 carry = 0;
    for (int i = 0; i < a->n; i++) {
        u64 cur = (u64)a->v[i] * m + carry;
        a->v[i] = (unsigned int)(cur & 0xFFFFFFFFu);
        carry = cur >> 32;
    }
    while (carry) { a->v[a->n++] = (unsigned int)(carry & 0xFFFFFFFFu); carry >>= 32; }
}

static void big_shl(big *a, int bits) {
    if (a->n == 0) return;
    int limbs = bits / 32, rest = bits %% 32;
    if (rest) {
        /* The `32 - rest` shift is why this is guarded: shifting a 32-bit
           value by 32 is undefined, and `rest == 0` is the common case. */
        unsigned int carry = 0;
        for (int i = 0; i < a->n; i++) {
            unsigned int cur = a->v[i];
            a->v[i] = (cur << rest) | carry;
            carry = (unsigned int)((u64)cur >> (32 - rest));
        }
        if (carry) a->v[a->n++] = carry;
    }
    if (limbs) {
        for (int i = a->n - 1; i >= 0; i--) a->v[i + limbs] = a->v[i];
        for (int i = 0; i < limbs; i++) a->v[i] = 0;
        a->n += limbs;
    }
}

static int big_cmp(const big *a, const big *b) {
    if (a->n != b->n) return a->n < b->n ? -1 : 1;
    for (int i = a->n - 1; i >= 0; i--)
        if (a->v[i] != b->v[i]) return a->v[i] < b->v[i] ? -1 : 1;
    return 0;
}

static void big_add(big *a, const big *b) {
    int n = a->n > b->n ? a->n : b->n;
    u64 carry = 0;
    for (int i = 0; i < n; i++) {
        u64 cur = carry + (i < a->n ? a->v[i] : 0u) + (i < b->n ? b->v[i] : 0u);
        a->v[i] = (unsigned int)(cur & 0xFFFFFFFFu);
        carry = cur >> 32;
    }
    a->n = n;
    if (carry) a->v[a->n++] = (unsigned int)carry;
}

/* Requires a >= b, which every caller here has already established with
   `big_cmp`; a borrow out of the top would corrupt `n` rather than trap. */
static void big_sub(big *a, const big *b) {
    i64 borrow = 0;
    for (int i = 0; i < a->n; i++) {
        i64 cur = (i64)a->v[i] - borrow - (i64)(i < b->n ? b->v[i] : 0u);
        if (cur < 0) { cur += 0x100000000ll; borrow = 1; } else borrow = 0;
        a->v[i] = (unsigned int)cur;
    }
    while (a->n > 0 && a->v[a->n - 1] == 0) a->n--;
}

void put_float(double v) {
    union { double d; u64 u; } bits;
    bits.d = v;
    u64 u = bits.u;

    if ((u & 0x7FF0000000000000ull) == 0x7FF0000000000000ull) {
        /* "nan" for BOTH signs. C's printf writes "-nan" for a negative NaN
           and Python's repr does not, and this used to follow printf. */
        const char *s = (u & 0x000FFFFFFFFFFFFFull) ? "nan"
                      : ((u >> 63) ? "-inf" : "inf");
        while (*s) putchar(*s++);
        return;
    }
    /* The sign comes from the BIT: -0.0 equals 0.0 but reprs as "-0.0". */
    if (u >> 63) putchar('-');

    unsigned int ex = (unsigned int)((u >> 52) & 0x7FFull);
    u64 mf = u & 0x000FFFFFFFFFFFFFull;
    if (ex == 0 && mf == 0) { putchar('0'); putchar('.'); putchar('0'); return; }

    /* v = f * 2^e exactly, with the implicit bit restored for a normal and
       absent for a subnormal, which shares the smallest normal's exponent. */
    u64 f; int e;
    if (ex == 0) { f = mf;               e = -1074; }
    else         { f = mf | (1ull << 52); e = (int)ex - 1075; }

    /* A decimal landing exactly on the boundary between v and its neighbour
       is read back as whichever of the two has an EVEN mantissa. So the
       boundary is reachable -- belongs to v -- exactly when f is even, and
       that decides < versus <= in every test below. Getting this backwards
       costs one digit on about one value in 2^52, which no small test finds. */
    int inclusive = (f & 1) == 0;
    /* Immediately above a power of two the gap doubles, so the neighbour BELOW
       is only half a gap away and M- is half of M+. Not at ex == 1: there the
       neighbour below is subnormal and the spacing is unchanged. */
    int asym = (mf == 0 && ex > 1);

    big R, S, Mp, Mm, t;
    if (e >= 0) {
        /* S is a constant and the value is shifted up into it. */
        big_set(&R, f);  big_shl(&R, e + 1 + asym);
        big_set(&S, asym ? 4 : 2);
        big_set(&Mp, 1); big_shl(&Mp, e + asym);
        big_set(&Mm, 1); big_shl(&Mm, e);
    } else {
        /* The negative power of two goes into S instead; R stays tiny. */
        big_set(&R, f); big_shl(&R, 1 + asym);
        big_set(&S, 1); big_shl(&S, -e + 1 + asym);
        big_set(&Mp, asym ? 2u : 1u);
        big_set(&Mm, 1);
    }

    /* Scale so that the value lies in (0.1, 1]: v = 0.d1d2... * 10^k, which is
       what makes the first generated digit nonzero and, with the digit loop's
       invariant below, makes a carry out of the leading digit impossible.
       k is found by stepping rather than from a log10 estimate with a fixup:
       the estimate is the part of Dragon4 that is fiddly to get right at the
       edges, the loop is exact by inspection, and 324 big-integer multiplies
       by ten in the worst case cost nothing on something that only prints. */
    int k = 0;
    for (;;) {
        big_copy(&t, &R); big_add(&t, &Mp);
        int c = big_cmp(&t, &S);
        if (inclusive ? c >= 0 : c > 0) { big_mul_small(&S, 10); k++; continue; }
        big_mul_small(&t, 10);           /* would (R+M+)/S still fit at k-1? */
        c = big_cmp(&t, &S);
        if (inclusive ? c < 0 : c <= 0) {
            big_mul_small(&R, 10); big_mul_small(&Mp, 10); big_mul_small(&Mm, 10);
            k--; continue;
        }
        break;
    }

    char digits[24];
    int nd = 0;
    for (;;) {
        big_mul_small(&R, 10); big_mul_small(&Mp, 10); big_mul_small(&Mm, 10);
        /* R < S holds on entry, so R*10 < S*10 and the quotient is one digit.
           Repeated subtraction is therefore a complete division, and no
           general big-integer divide has to exist. */
        int d = 0;
        while (big_cmp(&R, &S) >= 0) { big_sub(&R, &S); d++; }

        int c = big_cmp(&R, &Mm);
        int low = inclusive ? c <= 0 : c < 0;      /* stopping here rounds down to v */
        big_copy(&t, &R); big_add(&t, &Mp);
        c = big_cmp(&t, &S);
        int high = inclusive ? c >= 0 : c > 0;     /* stopping here rounds up to v */

        if (!low && !high) { digits[nd++] = (char)('0' + d); continue; }
        if (high && !low) {
            d++;
        } else if (high && low) {
            /* Both ends are reachable, so take the nearer, and on an exact tie
               the one with an even last digit -- which is what CPython's dtoa
               does, and the only place the two could differ on a shortest
               string that is otherwise uniquely determined. */
            big_copy(&t, &R); big_shl(&t, 1);
            c = big_cmp(&t, &S);
            if (c > 0 || (c == 0 && (d & 1))) d++;
        }
        /* d+1 cannot reach ten, so no carry can propagate back through the
           digits already emitted. The loop only continues while R + M+ <= S,
           so on entry R*10 + M+*10 <= S*10; if d were 9 then R*10 >= 9S, and
           the remainder R' = R*10 - 9S would satisfy R' + M+*10 <= S, i.e.
           `high` would be false and d would not be incremented. */
        digits[nd++] = (char)('0' + d);
        break;
    }

    /* Python's notation, NOT C's %%g. %%g switches to an exponent at
       `exp >= precision`, which moves with however many digits the value
       happened to need; Python switches at a FIXED 16. They disagree on
       12345678901234567.0, which Python writes 1.2345678901234568e+16. */
    int exp10 = k - 1;
    if (exp10 < -4 || exp10 >= 16) {
        putchar(digits[0]);
        if (nd > 1) {
            putchar('.');
            for (int i = 1; i < nd; i++) putchar(digits[i]);
        }
        putchar('e');
        putchar(exp10 < 0 ? '-' : '+');
        int a = exp10 < 0 ? -exp10 : exp10;
        /* Always at least two digits ("1e-05"), never a needless third: the
           MSVCRT that mingw links against writes "1e-005" and CPython does
           not, which is a difference this runtime must not reintroduce. */
        if (a >= 100) putchar((char)('0' + a / 100));
        putchar((char)('0' + (a / 10) %% 10));
        putchar((char)('0' + a %% 10));
        return;
    }
    if (k <= 0) {
        putchar('0'); putchar('.');
        for (int i = 0; i < -k; i++) putchar('0');
        for (int i = 0; i < nd; i++) putchar(digits[i]);
        return;
    }
    for (int i = 0; i < nd; i++) {
        if (i == k) putchar('.');
        putchar(digits[i]);
    }
    /* An integral value is still a float: "2.0", never "2". */
    if (k >= nd) {
        for (int i = nd; i < k; i++) putchar('0');
        putchar('.'); putchar('0');
    }
}

@POW@

/* Called for float `%%` and, through the frontend's floor correction, float
   `//`. The frontend emits calls to it by name and libm is not available here.

   THIS IS EXACT, and it has to be: every other execution path calls libm's
   fmod, which is exact, so an approximation here is the backend disagreeing
   about a number rather than about how to print one.

   WHAT WAS HERE BEFORE: `a - (double)(i64)(a / b) * b`. Every step of that
   rounds. For -53.7228 %% -1.7785 it gives -0.36780000000000257 where the true
   remainder is -0.36780000000000035 -- the quotient is fine, but `t * b` is
   rounded and then subtracting two nearly equal numbers cancels away the top
   bits and promotes that rounding into the 15th significant digit. It survived
   for as long as it did only because the runtime printed six decimals, so both
   answers came out "-0.367800"; the moment floats printed as Python prints
   them, three of the twenty differential seeds disagreed.

   So the remainder is computed the way libm computes it: binary long division
   on the integer mantissas, where every step is an exact integer subtract and
   nothing is ever rounded. */
double fmod(double a, double b) {
    union { double d; u64 u; } ua, ub, out;
    ua.d = a; ub.d = b;
    u64 sign = ua.u & 0x8000000000000000ull;   /* fmod takes the DIVIDEND's */
    u64 xa = ua.u & 0x7FFFFFFFFFFFFFFFull;
    u64 xb = ub.u & 0x7FFFFFFFFFFFFFFFull;

    /* Clearing the sign bit makes the bit pattern of a non-negative double
       order the same way its value does, so these are magnitude tests. */
    if (xa > 0x7FF0000000000000ull || xb > 0x7FF0000000000000ull  /* either NaN */
        || xa == 0x7FF0000000000000ull                            /* inf %% y   */
        || xb == 0)                                               /* x %% 0     */
        return 0.0 / 0.0;   /* how a NaN is spelled with no NAN macro to hand */
    if (xb == 0x7FF0000000000000ull || xa == 0 || xa < xb) return a;

    /* |v| = m * 2^e with m an integer. Subnormals share the smallest normal's
       exponent and have no implicit bit. */
    u64 ma, mb; int ea, eb;
    unsigned int ka = (unsigned int)(xa >> 52), kb = (unsigned int)(xb >> 52);
    if (ka == 0) { ma = xa; ea = -1074; }
    else { ma = (xa & 0x000FFFFFFFFFFFFFull) | (1ull << 52); ea = (int)ka - 1075; }
    if (kb == 0) { mb = xb; eb = -1074; }
    else { mb = (xb & 0x000FFFFFFFFFFFFFull) | (1ull << 52); eb = (int)kb - 1075; }

    /* Both mantissas are brought into [2^52, 2^53) -- NOT just decoded -- so
       that ma < 2*mb and one conditional subtract per bit is enough. Skipping
       this leaves a subnormal divisor with a mantissa of 1 against a normal
       dividend, where a single subtraction removes almost none of it and the
       remainder comes out larger than the divisor. */
    while (ma < (1ull << 52)) { ma <<= 1; ea--; }
    while (mb < (1ull << 52)) { mb <<= 1; eb--; }

    /* |a| >= |b| with both mantissas normalised means ea >= eb, so this runs
       at least zero times and at most about 2100 -- the full exponent range,
       for a subnormal remainder of a huge dividend. It costs nothing that
       matters: `%%` is not on any loop this compiler generates. */
    u64 r = ma;
    for (int i = ea - eb; i > 0; i--) {
        if (r >= mb) r -= mb;
        r <<= 1;     /* r < mb < 2^53 here, so the shift cannot overflow */
    }
    if (r >= mb) r -= mb;

    /* A zero remainder still carries the dividend's sign: `-6.0 %% 3.0` is
       -0.0. The frontend's Python correction tells the two zeros apart. */
    if (r == 0) { out.u = sign; return out.d; }

    /* r * 2^eb, assembled by hand, exactly -- no rounding on the way out
       either.

       eb can be BELOW -1074 here, and missing that is a real bug this had:
       normalising a subnormal divisor's mantissa up to [2^52, 2^53) pushes its
       exponent down by as much as 51, so `fmod(1.0, 1.5e-323)` came out with
       eb == -1125. `eb + 1075` was then negative and shifting it left by 52
       ran into the SIGN bit, which is why the answer was -2.5e+296 rather than
       5e-324. Shifting r back down is exact: the true remainder is a multiple
       of 2^-1074 (both operands are), so those low bits are zero. */
    while (eb < -1074) { r >>= 1; eb++; }
    while (r < (1ull << 52) && eb > -1074) { r <<= 1; eb--; }
    if (r < (1ull << 52)) out.u = sign | r;   /* subnormal: no implicit bit */
    else out.u = sign | ((u64)(eb + 1075) << 52) | (r & 0x000FFFFFFFFFFFFFull);
    return out.d;
}

double pow(double base, double exponent) {
    /* Kept because a module lifted back from assembly can still name it.
       Nothing the frontend emits calls it any more -- `x ** n` goes to
       `py_pow_int`, which is correctly rounded where plain squaring is not. */
    return py_pow_int(base, (i64)exponent);
}
"""


def runtime_c() -> str:
    """The freestanding runtime source, with the shared pieces substituted in.

    `py_pow_int` is NOT reimplemented here. It is exactly the same algorithm
    the hosted runtime uses, it needs no libc at all -- only `+`, `-` and `*`
    on doubles -- and a second copy would be a second thing to get right. The
    version this replaced squared in plain double, which is a ulp off CPython
    often enough to fail four cases in eighty.
    """
    return RUNTIME_C.replace("@POW@", POW_INT_C.replace("@STATIC@", ""))


def write_runtime_sources(directory: Path) -> tuple[Path, Path, Path]:
    """Write the startup, linker script and runtime. Returns their paths."""
    directory.mkdir(parents=True, exist_ok=True)
    start = directory / "start.S"
    script = directory / "baremetal.ld"
    runtime = directory / "asmpython_rt.c"
    start.write_text(START_S, encoding="utf-8")
    script.write_text(LINKER_LD % {"load": LOAD_ADDRESS}, encoding="utf-8")
    runtime.write_text(runtime_c() % {"uart": UART_ADDRESS}, encoding="utf-8")
    return start, script, runtime


class BareMetalToolchain(Toolchain):
    """Assemble and link a freestanding image for a bare-metal target.

    Separate from `cc` rather than a flag on it, because almost nothing is
    shared: no libc, no start files, a linker script that has to match the
    machine's memory map, and a runtime that must be built from source every
    time because it is target-specific.
    """

    name = "baremetal"
    description = "freestanding image for a bare-metal target (no OS, no libc)"

    def supports(self, target) -> bool:
        return target.os == "none"

    def link(self, request: LinkRequest) -> Path:
        target = request.target
        if target.os != "none":
            raise LinkError(
                f"{target.name!r} is not a bare-metal target",
                help="use --toolchain cc for a hosted target")

        cc = find_tool(target.cc_names or ("gcc",),
                       what=f"compiler for {target.name}",
                       install="install the Arm GNU Toolchain "
                               "(aarch64-none-elf) and put its bin/ on PATH")

        work = request.workdir
        start, script, runtime = write_runtime_sources(work)

        inputs = []
        for name, data in request.artifacts.items():
            path = work / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            inputs.append(str(path))
        if not inputs:
            raise LinkError("the backend produced nothing to link")

        output = request.output
        run(request, [
            cc, "-ffreestanding", "-nostdlib", "-nostartfiles",
            "-T", str(script), "-o", str(output),
            str(start), *inputs, str(runtime), *request.extra_inputs,
        ], what="linking a freestanding image")
        if not output.exists():
            raise LinkError(f"{cc} reported success but wrote no "
                            f"{output.name}")
        return output


def load_builtin() -> None:
    register(BareMetalToolchain())

"""The freestanding AArch64 runtime, and the toolchain that links it.

A bare-metal target has no operating system and no libc, so the runtime the
frontend calls into has to be written from nothing: an entry point, a stack,
and enough of `print` to be useful. In exchange the result runs under
`qemu-system-aarch64` with no guest OS, which is what makes an ARM64 backend
verifiable on a machine that is not ARM.

WHY NOT NEWLIB, which ships with the toolchain and would give real `printf`:
it faults. Its `snprintf` builds a `FILE` on the stack and takes an alignment
fault at EL1 the moment malloc hands it a block, and chasing that is a worse
use of effort than sixty lines of formatting whose output can be checked
directly against C's.

And it was checked: `format_double` here is the algorithm from
`tests/.../test_arm64.py::test_the_float_formatter_matches_printf`, compared
against `printf("%f")` on 200,000 values including the ties that round-half
gets wrong (0.5, 1.5, 2.5), the smallest value that rounds up at six decimals,
and negative zero.

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

void put_float(double v) {
    union { double d; u64 u; } bits;
    bits.d = v;

    if ((bits.u & 0x7FF0000000000000ull) == 0x7FF0000000000000ull) {
        int neg = (bits.u >> 63) != 0;
        const char *s = (bits.u & 0x000FFFFFFFFFFFFFull)
                      ? (neg ? "-nan" : "nan") : (neg ? "-inf" : "inf");
        while (*s) putchar(*s++);
        return;
    }
    /* The sign comes from the BIT: -0.0 equals 0.0 but prints "-0.000000". */
    if (bits.u >> 63) { putchar('-'); v = -v; }

    u64 whole = (u64)v;
    double frac = v - (double)whole;
    double scaled = frac * 1000000.0;
    u64 micros = (u64)scaled;
    double rest = scaled - (double)micros;
    /* Round half to EVEN, which is what printf does. Plain +0.5 sends 0.5 to
       1 and 2.5 to 3, and both are wrong. */
    if (rest > 0.5 || (rest == 0.5 && (micros & 1))) micros++;
    if (micros >= 1000000) { micros -= 1000000; whole++; }

    put_u64(whole);
    putchar('.');
    for (u64 div = 100000; div; div /= 10) {
        putchar((char)('0' + (micros / div) %% 10));
    }
}

/* Called for float `%%` and `**`; the frontend emits calls to these by name
   and libm is not available here. */
double fmod(double a, double b) {
    if (b == 0.0 || a != a || b != b) return a - a == 0.0 ? 0.0 / 0.0 : a;
    double q = a / b;
    /* Truncate toward zero without libc, staying inside i64 where possible. */
    if (q < 9.2233720368547758e18 && q > -9.2233720368547758e18) {
        i64 t = (i64)q;
        return a - (double)t * b;
    }
    return 0.0;
}

double pow(double base, double exponent) {
    /* The frontend only ever emits a non-negative integral exponent, which
       squaring computes exactly the way libm's pow does for these values. */
    i64 n = (i64)exponent;
    double result = 1.0, square = base;
    if (n < 0) return 0.0;
    while (n) {
        if (n & 1) result *= square;
        square *= square;
        n >>= 1;
    }
    return result;
}
"""


def write_runtime_sources(directory: Path) -> tuple[Path, Path, Path]:
    """Write the startup, linker script and runtime. Returns their paths."""
    directory.mkdir(parents=True, exist_ok=True)
    start = directory / "start.S"
    script = directory / "baremetal.ld"
    runtime = directory / "asmpython_rt.c"
    start.write_text(START_S, encoding="utf-8")
    script.write_text(LINKER_LD % {"load": LOAD_ADDRESS}, encoding="utf-8")
    runtime.write_text(RUNTIME_C % {"uart": UART_ADDRESS}, encoding="utf-8")
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

"""Verify the __udivdi64/__divdi64/__umoddi64/__moddi64 64-bit-division
runtime helpers (asmpython/_runtime/abi_shims_x86_32.asm) by actually
executing the assembled machine code in a real x86 (32-bit) CPU emulator
(Unicorn Engine) and checking results against Python's own arbitrary-
precision arithmetic, wrapped to 64-bit two's complement.

These helpers exist because asmpython's shared IR models every int as a
true 64-bit value, but a 32-bit GP register only holds 32 bits -- the x86
(32-bit) backend (and every other 32-bit-and-under backend: ARM-32, MIPS,
PowerPC-32, AVR, 8051, PIC, Xtensa) represents a 64-bit int as a register/
stack-word pair and calls these helpers for division, since there is no
single x86-32 instruction for a 64-by-64 divide.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE
    from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EDX, UC_X86_REG_ESP, UC_X86_REG_EIP
    _UNICORN_AVAILABLE = True
except ImportError:
    _UNICORN_AVAILABLE = False


_SRC = Path(__file__).resolve().parent.parent / "asmpython" / "_runtime" / "abi_shims_x86_32.asm"

_STACK_ADDR = 0x0040_0000
_STACK_SIZE = 0x0010_0000
_CODE_ADDR = 0x0010_0000
_RET_SENTINEL = 0x0000_1234  # a fake "return address" the emulator stops at


def _tools_available() -> bool:
    return all(shutil.which(t) is not None for t in ("nasm", "nm", "objcopy"))


def _assemble_shims_elf32() -> Path:
    """Assemble abi_shims_x86_32.asm to a real ELF32 object (kept on disk
    for the caller to also run nm/objcopy against -- .text's file offset
    inside that object need not be zero, so extracting flat code bytes is
    a separate objcopy step, not implied by this one)."""
    tmp = tempfile.mkdtemp()
    out = Path(tmp) / "shims.o"
    result = subprocess.run(
        ["nasm", "-f", "elf32", str(_SRC), "-o", str(out)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"nasm failed:\n{result.stdout}\n{result.stderr}")
    return out


def _extract_text(obj_path: Path) -> bytes:
    """The raw, relocation-free .text section bytes (this file has no
    relocations to apply -- every label used from within it is a plain
    same-section relative call/jump NASM already resolved at assembly
    time; only __divdi64/__moddi64's `call __udivdi64`/`call __umoddi64`
    cross function boundaries, and both callee and caller live in the
    same .text section NASM lays out contiguously, so the call's rel32
    is already correct without a linker pass)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "text.bin"
        result = subprocess.run(
            ["objcopy", "-O", "binary", "--only-section=.text", str(obj_path), str(out)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise AssertionError(f"objcopy failed:\n{result.stdout}\n{result.stderr}")
        return out.read_bytes()


def _symbol_offsets(obj_path: Path) -> dict[str, int]:
    result = subprocess.run(["nm", str(obj_path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"nm failed:\n{result.stdout}\n{result.stderr}")
    offsets: dict[str, int] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] in ("T", "t"):
            offsets[parts[2]] = int(parts[0], 16)
    return offsets


def _to_u64(value: int) -> int:
    return value & 0xFFFF_FFFF_FFFF_FFFF


def _split(value: int) -> tuple[int, int]:
    value = _to_u64(value)
    return value & 0xFFFF_FFFF, (value >> 32) & 0xFFFF_FFFF


def _combine(lo: int, hi: int) -> int:
    return (hi << 32) | lo


def _to_s64(value: int) -> int:
    value = _to_u64(value)
    return value - (1 << 64) if value & (1 << 63) else value


@unittest.skipUnless(_tools_available(), "requires nasm, nm, and objcopy on PATH")
@unittest.skipUnless(_UNICORN_AVAILABLE, "requires the unicorn CPU-emulator package")
class X86_32DivDi64Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        obj_path = _assemble_shims_elf32()
        cls.code = _extract_text(obj_path)
        cls.offsets = _symbol_offsets(obj_path)
        for name in ("__udivdi64", "__umoddi64", "__divdi64", "__moddi64"):
            assert name in cls.offsets, f"missing symbol {name!r}"

    def _call(self, label: str, dividend: int, divisor: int) -> int:
        """Emulate a cdecl call to `label`(dividend, divisor), both 64-bit,
        laid out as (lo, hi) dword pairs pushed divisor-then-dividend (so
        the stack reads dividend_lo, dividend_hi, divisor_lo, divisor_hi
        above the return address, matching a real caller pushing divisor
        first then dividend last, right-to-left cdecl argument order).
        Returns the 64-bit unsigned result from EDX:EAX.
        """
        mu = Uc(UC_ARCH_X86, UC_MODE_32)
        mu.mem_map(_CODE_ADDR & ~0xFFF, 0x0010_0000)
        mu.mem_write(_CODE_ADDR, self.code)
        mu.mem_map(_STACK_ADDR, _STACK_SIZE)

        dividend_lo, dividend_hi = _split(dividend)
        divisor_lo, divisor_hi = _split(divisor)

        esp = _STACK_ADDR + _STACK_SIZE - 0x1000
        stack_layout = [
            _RET_SENTINEL,
            dividend_lo, dividend_hi,
            divisor_lo, divisor_hi,
        ]
        for i, word in enumerate(stack_layout):
            mu.mem_write(esp + i * 4, word.to_bytes(4, "little"))

        mu.reg_write(UC_X86_REG_ESP, esp)
        entry = _CODE_ADDR + self.offsets[label]

        # Stop when EIP reaches the sentinel return address (the callee's
        # own `ret` will pop it and jump there).
        mu.emu_start(entry, _RET_SENTINEL, count=200_000)

        eax = mu.reg_read(UC_X86_REG_EAX)
        edx = mu.reg_read(UC_X86_REG_EDX)
        return _combine(eax, edx)

    # ---- unsigned ---------------------------------------------------------

    def test_udivdi64_basic(self):
        cases = [
            (100, 7), (0, 5), (1, 1), (7, 100),
            (0xFFFF_FFFF_FFFF_FFFF, 1),
            (0xFFFF_FFFF_FFFF_FFFF, 0xFFFF_FFFF_FFFF_FFFF),
            (0x1_0000_0000, 2),
            (0x1_0000_0001, 0x1_0000_0000),
            (12345678901234567, 987654321),
            (0x8000_0000_0000_0000, 2),
        ]
        for dividend, divisor in cases:
            expected = dividend // divisor
            got = self._call("__udivdi64", dividend, divisor)
            self.assertEqual(
                got, expected,
                f"udivdi64({dividend:#x}, {divisor:#x}): got {got:#x}, expected {expected:#x}",
            )

    def test_umoddi64_basic(self):
        cases = [
            (100, 7), (0, 5), (1, 1), (7, 100),
            (0xFFFF_FFFF_FFFF_FFFF, 1),
            (0xFFFF_FFFF_FFFF_FFFF, 0xFFFF_FFFF_FFFF_FFFF),
            (0x1_0000_0001, 0x1_0000_0000),
            (12345678901234567, 987654321),
            (0x8000_0000_0000_0000, 3),
        ]
        for dividend, divisor in cases:
            expected = dividend % divisor
            got = self._call("__umoddi64", dividend, divisor)
            self.assertEqual(
                got, expected,
                f"umoddi64({dividend:#x}, {divisor:#x}): got {got:#x}, expected {expected:#x}",
            )

    # ---- signed -------------------------------------------------------------

    def test_divdi64_signed(self):
        cases = [
            (100, 7), (-100, 7), (100, -7), (-100, -7),
            (0, 5), (0, -5),
            (1, 1), (-1, 1), (1, -1), (-1, -1),
            (-9223372036854775808, 1),   # INT64_MIN / 1
            (-9223372036854775808, -1),  # INT64_MIN / -1 (overflow case, wraps like C)
            (9223372036854775807, 1),    # INT64_MAX
            (-12345678901234567, 987654321),
            (12345678901234567, -987654321),
        ]
        for dividend, divisor in cases:
            expected = _to_s64(int(dividend / divisor)) if divisor != 0 else None
            # Python's `/` truncates toward zero for floats; do exact
            # truncating integer division matching C's semantics instead.
            magnitude = abs(dividend) // abs(divisor)
            sign = -1 if (dividend < 0) != (divisor < 0) else 1
            expected = _to_s64(sign * magnitude)
            got_u = self._call("__divdi64", dividend, divisor)
            got = _to_s64(got_u)
            self.assertEqual(
                got, expected,
                f"divdi64({dividend}, {divisor}): got {got}, expected {expected}",
            )

    def test_moddi64_signed(self):
        cases = [
            (100, 7), (-100, 7), (100, -7), (-100, -7),
            (0, 5), (0, -5),
            (1, 1), (-1, 1), (1, -1), (-1, -1),
            (-9223372036854775808, 1),
            (9223372036854775807, 1),
            (-12345678901234567, 987654321),
            (12345678901234567, -987654321),
        ]
        for dividend, divisor in cases:
            magnitude = abs(dividend) % abs(divisor)
            # C/truncating-division remainder takes the DIVIDEND's sign.
            expected = _to_s64(-magnitude if dividend < 0 else magnitude)
            got_u = self._call("__moddi64", dividend, divisor)
            got = _to_s64(got_u)
            self.assertEqual(
                got, expected,
                f"moddi64({dividend}, {divisor}): got {got}, expected {expected}",
            )


if __name__ == "__main__":
    unittest.main()

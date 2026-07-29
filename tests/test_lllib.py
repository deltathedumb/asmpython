"""lllib's portable implementations, checked against CPython's own semantics.

Every operation exists more than once -- Python here, APC in
``_frontends/apc/std/bits.apc``, and eventually a backend instruction. Which
one answers is meant to be a performance question and never a correctness one,
so they are held to the same cases.

That is not theoretical: differential-testing the two found a real bug. APC's
``rotl`` shifted in a 64-bit register without masking back down to ``width``,
so ``rotl(0x80000001, 1, 32)`` returned ``0x1_00000003`` instead of ``0x3``.
Neither implementation alone would have shown it.
"""

from __future__ import annotations

import unittest

from asmpython import lllib
from asmpython.lllib import bits, endian

WIDTHS = (8, 16, 32, 64)


def _values(width: int) -> "list[int]":
    top = (1 << width) - 1
    return [0, 1, 2, 3, 5, 0x0F, 0x80, 255 & top,
            1 << (width - 1), top, top >> 1, 0xA5A5A5A5 & top]


class BitsAgainstCPythonTests(unittest.TestCase):
    """CPython is the oracle: `bin().count`, `bit_length`, `int.to_bytes`."""

    def test_popcount(self) -> None:
        for w in WIDTHS:
            for v in _values(w):
                with self.subTest(w=w, v=v):
                    self.assertEqual(bits.popcount(v, w), bin(v).count("1"))

    def test_clz_and_bit_length(self) -> None:
        for w in WIDTHS:
            for v in _values(w):
                with self.subTest(w=w, v=v):
                    self.assertEqual(bits.clz(v, w), w - v.bit_length())
                    self.assertEqual(bits.bit_length(v, w), v.bit_length())

    def test_ctz(self) -> None:
        for w in WIDTHS:
            for v in _values(w):
                want = w if v == 0 else (v & -v).bit_length() - 1
                with self.subTest(w=w, v=v):
                    self.assertEqual(bits.ctz(v, w), want)

    def test_clz_and_ctz_of_zero_are_the_width(self) -> None:
        """The boundary the hardware instructions also have to define."""
        for w in WIDTHS:
            self.assertEqual(bits.clz(0, w), w)
            self.assertEqual(bits.ctz(0, w), w)

    def test_byteswap(self) -> None:
        for w in (8, 16, 32, 64):
            for v in _values(w):
                want = int.from_bytes(v.to_bytes(w // 8, "little"), "big")
                with self.subTest(w=w, v=v):
                    self.assertEqual(bits.byteswap(v, w), want)

    def test_rotate_is_reversible_and_width_bounded(self) -> None:
        for w in WIDTHS:
            for v in _values(w):
                for n in (0, 1, 7, w - 1, w, w + 1):
                    with self.subTest(w=w, v=v, n=n):
                        rotated = bits.rotl(v, n, w)
                        self.assertLessEqual(rotated, (1 << w) - 1)
                        self.assertEqual(bits.rotr(rotated, n, w), v)

    def test_rotate_known_values(self) -> None:
        """The exact case APC got wrong by not masking to width."""
        self.assertEqual(bits.rotl(0x80000001, 1, 32), 0x3)
        self.assertEqual(bits.rotr(0x3, 1, 32), 0x80000001)
        self.assertEqual(bits.rotl(1, 33, 32), 2)      # amount wraps
        self.assertEqual(bits.rotl(0xF, 4, 8), 0xF0)

    def test_reverse_bits_is_an_involution(self) -> None:
        for w in WIDTHS:
            for v in _values(w):
                with self.subTest(w=w, v=v):
                    self.assertEqual(bits.reverse_bits(bits.reverse_bits(v, w), w), v)

    def test_sign_extend(self) -> None:
        self.assertEqual(bits.sign_extend(0xFF, 8, 64), (1 << 64) - 1)
        self.assertEqual(bits.sign_extend(0x7F, 8, 64), 0x7F)
        self.assertEqual(bits.sign_extend(0x8000, 16, 32), 0xFFFF8000)

    def test_alignment(self) -> None:
        self.assertEqual(bits.align_up(13, 8), 16)
        self.assertEqual(bits.align_up(16, 8), 16)
        self.assertEqual(bits.align_down(13, 8), 8)
        self.assertEqual([bits.is_power_of_two(x) for x in (0, 1, 2, 3, 64)],
                         [False, True, True, False, True])


class EndianTests(unittest.TestCase):
    def test_pack_and_unpack_match_cpython(self) -> None:
        for order, native in ((endian.LITTLE, "little"), (endian.BIG, "big")):
            for width in (1, 2, 4, 8):
                value = 0x0123456789ABCDEF & ((1 << width * 8) - 1)
                with self.subTest(order=order, width=width):
                    packed = endian.pack(value, width, order)
                    self.assertEqual(packed, value.to_bytes(width, native))
                    self.assertEqual(endian.unpack(packed, 0, width, order), value)

    def test_unpack_signed(self) -> None:
        self.assertEqual(endian.unpack_signed(b"\xff", 0, 1), -1)
        self.assertEqual(endian.unpack_signed(b"\x7f", 0, 1), 127)
        self.assertEqual(endian.unpack_signed(b"\x00\x80", 0, 2), -32768)

    def test_unpack_honours_offset(self) -> None:
        data = b"\xaa\xbb\x34\x12"
        self.assertEqual(endian.unpack(data, 2, 2, endian.LITTLE), 0x1234)
        self.assertEqual(endian.unpack(data, 2, 2, endian.BIG), 0x3412)

    def test_byte_order_is_explicit_not_inherited(self) -> None:
        """A format that says big-endian means it on every host."""
        self.assertNotEqual(endian.pack(1, 4, endian.LITTLE),
                            endian.pack(1, 4, endian.BIG))


class BackendSurfaceTests(unittest.TestCase):
    def test_portable_surface_needs_no_backend(self) -> None:
        self.assertTrue(hasattr(lllib, "bits"))
        self.assertTrue(hasattr(lllib, "endian"))

    def test_backends_opt_in_with_one_symbol(self) -> None:
        names = lllib.backends()
        self.assertIn("x86_64", names)
        for name in names:
            self.assertIsNotNone(lllib.for_backend(name))

    def test_unknown_backend_reports_what_is_available(self) -> None:
        with self.assertRaises(AttributeError) as ctx:
            lllib.for_backend("nonesuch")
        self.assertIn("available:", str(ctx.exception))

    def test_implementation_of_names_the_layer(self) -> None:
        """A build can assert it got the layer it expected, rather than
        silently falling back to the portable one inside a hot loop."""
        answer = lllib.implementation_of("popcount")
        self.assertTrue(
            answer == "python" or answer == "apc"
            or answer.startswith("backend:"), answer)

    def test_implementation_of_rejects_unknown_operations(self) -> None:
        with self.assertRaises(AttributeError):
            lllib.implementation_of("no_such_operation")



class IntrinsicTests(unittest.TestCase):
    """The backend rung: operations the machine does in one instruction.

    Encodings are checked against the Intel SDM by hand rather than against
    the encoder's own output, which would be circular.
    """

    def test_popcnt_and_bswap_encodings(self) -> None:
        from asmpython._backends.x86_64.encoder import (
            Reg, encode_bswap_r, encode_popcnt_rr,
        )
        self.assertEqual(encode_popcnt_rr(Reg.RAX, Reg.RBX).hex(), "f3480fb8c3")
        self.assertEqual(encode_popcnt_rr(Reg.R8, Reg.R9).hex(), "f34d0fb8c1")
        self.assertEqual(encode_bswap_r(Reg.RAX).hex(), "480fc8")
        self.assertEqual(encode_bswap_r(Reg.R12).hex(), "490fcc")

    def test_x86_64_declares_what_it_implements(self) -> None:
        self.assertEqual(lllib.implementation_of("popcount"), "backend:x86_64")
        self.assertEqual(lllib.implementation_of("byteswap"), "backend:x86_64")

    def test_operations_without_an_instruction_stay_portable(self) -> None:
        """clz has no safe x86 encoding (LZCNT aliases BSR without BMI1), so
        it must report the portable rung rather than claiming an intrinsic."""
        self.assertEqual(lllib.implementation_of("clz"), "python")
        self.assertEqual(lllib.implementation_of("rotl"), "python")

    def test_a_backend_without_intrinsics_reports_portable(self) -> None:
        self.assertEqual(lllib.implementation_of("popcount", backend="jvm"),
                         "python")

if __name__ == "__main__":
    unittest.main()

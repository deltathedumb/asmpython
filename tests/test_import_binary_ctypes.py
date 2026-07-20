from __future__ import annotations

import ctypes
import sys
import unittest
from unittest import mock

from asmpython import import_binary


class _FakeNativeFunction:
    def __init__(self, result):
        self.argtypes = None
        self.restype = None
        self.calls = []
        self.result = result

    def __call__(self, *args):
        self.calls.append(args)
        return self.result


class _FakeLibrary:
    def __init__(self):
        self.echo = _FakeNativeFunction(b"hello")


class ImportBinaryCtypesTests(unittest.TestCase):
    def test_imported_decorator_configures_ctypes_and_converts_strings(self) -> None:
        fake = _FakeLibrary()
        with mock.patch("asmpython._ctypes.CDLL", return_value=fake) as cdll:
            library = import_binary("example-library")

            @library.imported
            def echo(value: str, repeat: int = 1) -> str:
                pass

        cdll.assert_called_once_with("example-library")
        self.assertIs(library.echo, echo)
        self.assertEqual(fake.echo.argtypes, [ctypes.c_char_p, ctypes.c_int64])
        self.assertIs(fake.echo.restype, ctypes.c_char_p)
        self.assertEqual(echo(value="hello"), "hello")
        self.assertEqual(fake.echo.calls, [(b"hello", 1)])

    def test_missing_annotations_are_rejected_before_native_call(self) -> None:
        fake = _FakeLibrary()
        with mock.patch("asmpython._ctypes.CDLL", return_value=fake):
            library = import_binary("example-library")

            with self.assertRaisesRegex(TypeError, "must annotate parameter 'value'"):

                @library.imported
                def echo(value) -> int:
                    pass

    def test_real_c_runtime_smoke(self) -> None:
        if sys.platform == "win32":
            path = "msvcrt.dll"
        elif sys.platform.startswith("linux"):
            path = "libc.so.6"
        else:
            self.skipTest("the compiler currently targets Windows and Linux")

        library = import_binary(path)

        @library.imported
        def toupper(value: int) -> int:
            pass

        self.assertEqual(toupper(97), 65)
        self.assertEqual(library.toupper(122), 90)


if __name__ == "__main__":
    unittest.main()

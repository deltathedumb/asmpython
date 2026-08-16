"""Unit coverage for the public plugin-authoring API
(`asmpython/backend.py`/`linker.py`): `Backend`/`Linker` registration and
retrieval via ``get_backend``/``get_linker``.

The `Extension` (syntax-extension) half of this file was removed: the whole
compiler-extension system was withdrawn (see commit "withdraw the
compiler-extension system") -- asmpython.extend.Extension no longer exists,
and its real archived tests live in archived/extensions/test_extensions.py
for reference only, unwired from the compiler.

Run: python -m unittest tests.test_extend
"""

from __future__ import annotations

import unittest

import asmpython
from asmpython._backends import get_backend
from asmpython._linkers import get_linker


class BackendLinkerAuthoringTests(unittest.TestCase):
    def test_backend_registers_and_is_retrievable(self) -> None:
        class _DummyBackend:
            requested_args: list = []
            default_linker = "gcc"

            def compile(self, module, args):
                return {"x.o": b"stub"}

            def link(self, objects, args):
                return {"output": b"stub-exe"}

        impl = _DummyBackend()
        asmpython.backend.Backend(name="test_dummy_backend", impl=impl)
        # get_backend() returns Backend's own _ConfiguredBackend wrapper
        # (injects build options and build-report tracing around every
        # compile()/link() call), not the raw impl object -- so identity
        # isn't the right check; behavior delegating through to impl is.
        registered = get_backend("test_dummy_backend")
        self.assertIsNot(registered, impl)
        self.assertEqual(registered.name, "test_dummy_backend")
        self.assertEqual(registered.compile(object(), {}), {"x.o": b"stub"})
        self.assertEqual(registered.link([], {}), {"output": b"stub-exe"})

    def test_linker_registers_and_is_retrievable(self) -> None:
        class _DummyLinker:
            requested_args: list = []

            def link(self, ctx):
                return b"linked"

        impl = _DummyLinker()
        asmpython.linker.Linker(name="test_dummy_linker", impl=impl)
        # Same _Configured*-wrapper contract as Backend above.
        registered = get_linker("test_dummy_linker")
        self.assertIsNot(registered, impl)
        self.assertEqual(registered.name, "test_dummy_linker")
        self.assertEqual(registered.link({}), b"linked")


if __name__ == "__main__":
    unittest.main()

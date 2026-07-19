from __future__ import annotations

import unittest

from asmpython._compiler import ir_lower
from asmpython._compiler.unpack_normalize import install_ir_lowering_prepass


class TypedUnpackNormalizerInstallTests(unittest.TestCase):
    def test_compiler_package_installs_prepass(self) -> None:
        self.assertTrue(
            getattr(ir_lower, "_typed_unpack_normalizer_installed", False)
        )

    def test_installer_is_idempotent(self) -> None:
        installed = ir_lower.lower_module
        install_ir_lowering_prepass()
        self.assertIs(ir_lower.lower_module, installed)


if __name__ == "__main__":
    unittest.main()

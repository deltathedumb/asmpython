from __future__ import annotations

import struct
import unittest

import asmpython._backends.arm64 as arm64
from asmpython._compiler.ir import I64, IRBlock, IRFunc, IRInstr, IRModule, IRValue


class Arm64ModuleCodegenTests(unittest.TestCase):
    @staticmethod
    def _add_module() -> IRModule:
        left = IRValue("left", I64)
        right = IRValue("right", I64)
        result = IRValue("result", I64)
        func = IRFunc(
            "add_two",
            [left, right],
            I64,
            [
                IRBlock(
                    "entry",
                    [
                        IRInstr("iadd", result, [left, right]),
                        IRInstr("ret", None, [result]),
                    ],
                )
            ],
        )
        return IRModule(funcs=[func])

    def test_module_compiles_to_aarch64_elf_object(self) -> None:
        output = arm64.run_backend_codegen(
            self._add_module(),
            {"target_os": "linux", "abi": "aapcs64"},
        )

        self.assertEqual(set(output), {"output.o"})
        blob = output["output.o"]
        ident, elf_type, machine = struct.unpack_from("<16sHH", blob, 0)
        self.assertEqual(ident[:4], b"\x7fELF")
        self.assertEqual(elf_type, 1)  # ET_REL
        self.assertEqual(machine, 183)  # EM_AARCH64

    def test_unknown_ir_operation_is_rejected_with_context(self) -> None:
        result = IRValue("result", I64)
        module = IRModule(
            funcs=[
                IRFunc(
                    "bad_function",
                    [],
                    I64,
                    [
                        IRBlock(
                            "bad_block",
                            [IRInstr("future.op", result, [])],
                        )
                    ],
                )
            ]
        )

        with self.assertRaisesRegex(
            ValueError,
            r"future\.op.*bad_function.*bad_block.*instruction 0",
        ):
            arm64.compile_ir_module(module)

    def test_non_linux_target_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "target_os='linux' only"):
            arm64.run_backend_codegen(
                self._add_module(),
                {"target_os": "windows", "abi": "aapcs64"},
            )

    def test_unknown_abi_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "only supports ABI 'aapcs64'"):
            arm64.compile_ir_module(self._add_module(), abi="win64")

    def test_backend_is_not_advertised_before_runtime_exists(self) -> None:
        self.assertFalse(hasattr(arm64, "__module_backend__"))


if __name__ == "__main__":
    unittest.main()

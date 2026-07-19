from __future__ import annotations

from asmpython._compiler.target_linux import LinuxCodegen


def test_list_repeat_malloc_sizes_use_sysv_rdi() -> None:
    codegen = object.__new__(LinuxCodegen)
    codegen.lines = []
    codegen.label_counter = 0

    codegen._emit_list_repeat_helper()
    assembly = "\n".join(codegen.lines)

    assert "mov rdi, 24\ncall malloc" in assembly
    assert "mov rdi, 32\ncall malloc" in assembly
    assert "mov rcx, 24\ncall malloc" not in assembly
    assert "mov rcx, 32\ncall malloc" not in assembly

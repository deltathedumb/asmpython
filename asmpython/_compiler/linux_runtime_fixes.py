"""Linux-only corrections for shared runtime helpers.

Keep target-specific ABI fixes here when the common helper emitter cannot use a
platform-neutral primitive yet.  Importing this module patches LinuxCodegen
before the compiler driver imports it.
"""

from __future__ import annotations

from .target_linux import LinuxCodegen


def _emit_list_repeat_helper(self: LinuxCodegen) -> None:
    """Emit list repetition with SysV-correct malloc arguments.

    The common implementation historically loaded allocation sizes into RCX,
    which is correct for Win64 but not System V AMD64.  Route allocations
    through LinuxCodegen._emit_malloc so the size is placed in RDI.
    """
    self.label("_runtime_list_repeat")
    self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
    self.emitf(
        "mov [rbp-8], rax",
        "mov [rbp-16], rbx",
    )

    self._emit_malloc(24)
    self.emitf(
        "mov qword [rax+0], 4",
        "mov qword [rax+8], 0",
        "mov [rbp-24], rax",
    )
    self._emit_malloc(32)
    self.emitf(
        "mov rbx, [rbp-24]",
        "mov [rbx+16], rax",
    )

    self.emitf("mov qword [rbp-32], 0")
    top = self.fresh("lrep_top")
    done = self.fresh("lrep_done")
    self.label(top)
    self.emitf(
        "mov rax, [rbp-32]",
        "cmp rax, [rbp-16]",
        f"jge {done}",
        "mov rax, [rbp-24]",
        "mov rbx, [rbp-8]",
        "call _runtime_list_extend",
        "mov [rbp-24], rax",
        "inc qword [rbp-32]",
        f"jmp {top}",
    )
    self.label(done)
    self.emitf("mov rax, [rbp-24]", "leave", "ret")


LinuxCodegen._emit_list_repeat_helper = _emit_list_repeat_helper  # type: ignore[method-assign]

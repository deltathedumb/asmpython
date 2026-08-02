"""
asmpython ternary backend (driver.py's --backend ternary).

Compiles an IRModule to a flat binary image loadable by the ternary
CPU simulator (cpu.py / TernarySystem). Each 4-byte little-endian
signed integer in the output represents one 8-trit balanced ternary
memory cell.

Memory layout of the generated image
-------------------------------------
  0     CALL  #main              (4 words)
  4     HALT                     (2 words)
  6+    function code, in IR order
  FRAME_BASE+ static locals (one cell per alloca slot, per function)

Calling convention (see codegen.py for details)
-------------------------------------------------
  r0  = arg0 / return value
  r1  = arg1
  r2  = arg2
  r3  = arg3
  r4-r14 = temporaries (caller-saved)

IO
---
  print(int x)  →  OUT port 0, r_x
  The simulator's io_out list accumulates (0, value) tuples; the
  caller is expected to print them.

Plugin interface (asmpython._compiler.ssa.ir.ModuleBackend)
---------------------------------------------------------
  requested_args      list[dict]
  default_linker      str
  run_backend_codegen(ir, args) -> dict[str, bytes]
  run_backend_link(objects, args) -> dict[str, bytes]
  __module_backend__  ModuleBackend
"""

from __future__ import annotations

import sys
from typing import Any

from . import encoder as E
from .codegen import TernaryFuncCodegen
from ..._compiler.ssa.ir import ModuleBackend
from ..._compiler.unpack_normalize import install_ir_lowering_prepass


# driver.py imports ir_lower before resolving this backend. Install the same
# target-neutral typed-unpack prepass used by x86-64 before lower_module runs.
install_ir_lowering_prepass()


# ── CLI args ──────────────────────────────────────────────────────────────────

requested_args: list[dict] = [
    {"name": "--load-addr", "type": int, "default": 0,
     "help": "Base RAM address where the binary will be loaded (default 0)."},
    {"name": "--frame-addr", "type": int, "default": 4000,
     "help": "Base RAM address for static alloca slots (default 4000)."},
    {"name": "--lib", "action": "store_true", "default": False,
     "help": "Emit RET instead of HALT in entry stub (for programs loaded by kernel)."},
]
default_linker = "builtin"        # no external linker needed

_FRAME_BASE = 4000  # kept as fallback; overridden by args["frame_addr"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _count_allocas(func) -> int:
    return sum(
        1 for b in func.blocks for i in b.instrs if i.op == "alloca"
    )


def _find_main(funcs) -> int:
    for idx, f in enumerate(funcs):
        if f.name == "main":
            return idx
    return 0   # fall back to first function


# ── Code generation ───────────────────────────────────────────────────────────

def run_backend_codegen(ir: Any, args: dict) -> dict[str, bytes]:
    """
    Compile IRModule → flat ternary binary image.

    Recognised keys in args:
      load_addr  (int, default 0)    — RAM address where the binary will be loaded.
                                        All absolute jump/call targets are offset by this.
      frame_addr (int, default 4000) — Base RAM address for static alloca slots.
      lib        (bool, default False)— Use RET instead of HALT in entry stub so
                                        the caller (kernel) regains control after main().

    Returns {"output.tern": <bytes>} where each 4-byte LE signed int is one
    balanced-ternary memory cell.
    """
    if not ir.funcs:
        return {"output.tern": b""}

    load_addr  = int(args.get("load_addr",  args.get("load-addr",  0)))
    frame_base = int(args.get("frame_addr", args.get("frame-addr", _FRAME_BASE)))
    use_lib    = bool(args.get("lib", False))

    global_data: dict[str, object] = {
        g.name: g.value for g in getattr(ir, "data", [])
    }

    # Pre-scan: count alloca slots per function, assign static frame addresses.
    alloca_counts = [_count_allocas(f) for f in ir.funcs]
    frame_addrs: list[int] = []
    cursor = frame_base
    for cnt in alloca_counts:
        frame_addrs.append(cursor)
        cursor += max(cnt, 1)

    # Reorder: main first (comes right after the entry stub).
    main_idx = _find_main(ir.funcs)
    order = [main_idx] + [i for i in range(len(ir.funcs)) if i != main_idx]
    funcs_ordered = [ir.funcs[i] for i in order]
    frames_ordered = [frame_addrs[i] for i in order]

    # ── First pass: emit function words ──────────────────────────────────────
    # Entry stub (new format, 3 words):
    #   word 0: CALL header
    #   word 1: CALL target  (absolute RAM address of main)
    #   word 2: HALT or RET
    # With load_addr, the stub in RAM sits at [load_addr .. load_addr+2].
    # main in RAM starts at load_addr + 3.
    _STUB_SIZE = 3  # CALL(2 words) + HALT/RET(1 word)

    func_start_addr: dict[str, int] = {}
    gens: list[TernaryFuncCodegen] = []
    all_words: list[int] = [0] * _STUB_SIZE

    code_cursor = _STUB_SIZE  # offset within the binary (0-based)
    for func, faddr in zip(funcs_ordered, frames_ordered):
        # Absolute RAM address = binary offset + load_addr
        func_start_addr[func.name] = code_cursor + load_addr
        gen = TernaryFuncCodegen(func, code_cursor + load_addr, faddr, global_data)
        words = gen.lower()
        all_words.extend(words)
        code_cursor += len(words)
        gens.append(gen)

    # ── Second pass: resolve fixups ───────────────────────────────────────────
    for gen in gens:
        gen.resolve_fixups(func_start_addr)
        # Splice resolved words back; gen.func_addr is absolute RAM addr.
        bin_start = gen.func_addr - load_addr
        for i, w in enumerate(gen.words):
            all_words[bin_start + i] = w

    # ── Patch entry stub ──────────────────────────────────────────────────────
    main_addr = func_start_addr[funcs_ordered[0].name]  # absolute RAM address
    tail = E.ret_() if use_lib else E.halt()
    stub_words = E.call_abs(main_addr) + tail
    assert len(stub_words) == _STUB_SIZE, f"{len(stub_words)} != {_STUB_SIZE}"
    for i, w in enumerate(stub_words):
        all_words[i] = w

    return {"output.tern": E.words_to_bytes(all_words)}


# ── Linking ───────────────────────────────────────────────────────────────────

def run_backend_link(objects: list[bytes], args: dict) -> dict[str, bytes]:
    """
    Ternary images are self-contained; linking just returns the first
    (and only) object unchanged.
    """
    if not objects:
        return {"output.tern": b""}
    return {"output.tern": objects[0]}


__module_backend__ = ModuleBackend(sys.modules[__name__])

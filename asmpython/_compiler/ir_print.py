"""Readable text for an :class:`IRModule` -- the compiler's own output, visible.

There was no way to see the IR. Both of the hardest bugs found on this branch
were investigated by writing throwaway scripts that monkeypatched
``ir_lower.lower_module`` or re-ran the register allocator by hand, because the
alternative was guessing. That is the wrong default for a compiler.

Set ``ASMPYTHON_EMIT_IR`` to a path, or to ``-`` for stderr, and the driver
dumps the module **after** passes and immediately before the backend consumes
it -- the exact bytes codegen sees::

    ASMPYTHON_EMIT_IR=- asmpython build prog.py --passes o2
    ASMPYTHON_EMIT_IR=out.ir asmpython build prog.apc --frontend apc

Set ``ASMPYTHON_EMIT_ALLOC=1`` as well to annotate every value with where the
x86-64 register allocator put it, which is what turns "the answer is wrong"
into "these two live values are both in RDI".

The dump is a **round-trip** format: :func:`parse_module` reads it back into an
``IRModule`` that generates byte-identical machine code. So a miscompile can be
bisected by dumping the IR, editing one instruction, and feeding it back --
rather than hunting for source that happens to lower to the shape you want.

Note this is the neutral SSA IR. ``irfreeze`` serializes a different layer, the
typed AST that ``ir_lower`` consumes, under the same "asmpython-ir" name.
"""

from __future__ import annotations

import os
from typing import Any

from .ir import IRModule


def _operand(op: Any) -> str:
    if hasattr(op, "name") and hasattr(op, "type"):
        return f"%{op.name}"
    if isinstance(op, str):
        return op
    if isinstance(op, float):
        return repr(op)
    return str(op)


def _instr(instr: Any, locs: "dict[str, Any] | None") -> str:
    args = ", ".join(_operand(o) for o in (instr.operands or []))
    if instr.result is None:
        return f"    {instr.op}{' ' + args if args else ''}"
    where = ""
    if locs is not None:
        loc = locs.get(instr.result.name)
        if loc is not None:
            where = f"    ; {_loc(loc)}"
    return (f"    %{instr.result.name}: {instr.result.type.name} = "
            f"{instr.op}{' ' + args if args else ''}{where}")


def _loc(loc: Any) -> str:
    for attr in ("reg", "offset"):
        value = getattr(loc, attr, None)
        if value is not None:
            return getattr(value, "name", None) or f"rbp{value:+d}"
    return str(loc)


def format_func(func: Any, locs: "dict[str, Any] | None" = None) -> str:
    params = ", ".join(f"%{p.name}: {p.type.name}" for p in func.params)
    ret = func.ret_type.name if func.ret_type is not None else "none"
    vis = getattr(getattr(func, "visibility", None), "value", None)
    header = f"func {func.name}({params}) -> {ret}"
    if vis and vis != "undefined":
        header = f"{vis} {header}"

    lines = [header + " {"]
    for bi, block in enumerate(func.blocks):
        lines.append(f"  {block.label}:            ; block {bi}")
        for instr in block.instrs:
            lines.append(_instr(instr, locs))
        if not block.instrs:
            lines.append("    ; (empty)")
    for start, members in getattr(func, "try_regions", ()) or ():
        lines.append(f"  ; try region {start} -> {members}")
    lines.append("}")
    return "\n".join(lines)


def format_module(module: IRModule, *, with_alloc: bool = False,
                  abi: str = "sysv") -> str:
    out: list[str] = [
        f"; {len(module.funcs)} func(s), {len(module.data)} global(s)"
    ]
    if module.exports:
        out.append(f"exports {', '.join(module.exports)}")
    for glob in module.data:
        value = "" if glob.value is None else f" = {glob.value!r}"
        tls = " tls" if getattr(glob, "tls", False) else ""
        out.append(f"@{glob.name}: {glob.type.name}{tls}{value}")
    if module.data or module.exports:
        out.append("")

    for func in module.funcs:
        locs = None
        if with_alloc:
            try:
                from .._backends.x86_64.regalloc import allocate

                locs = allocate(func, abi).locs
            except Exception as exc:                # noqa: BLE001 - diagnostic
                out.append(f"; allocation failed for {func.name}: {exc!r}")
        out.append(format_func(func, locs))
        out.append("")
    return "\n".join(out)


# ── reading it back ──────────────────────────────────────────────────────────
#
# The dump is the round-trip format, not a separate one. That means a module
# can be dumped, hand-edited, and fed back -- which is how you bisect a
# miscompile down to a single instruction without hunting for source that
# happens to lower to the shape you want.
#
# Allocation annotations (`; RAX`) and the block-index comments are comments,
# so they are dropped on the way in and regenerated on the way out.

class IRParseError(ValueError):
    """A dump that could not be read back, with the offending line."""


def _strip_comment(line: str) -> str:
    out: list[str] = []
    in_string = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"' and (i == 0 or line[i - 1] != "\\"):
            in_string = not in_string
        elif ch == ";" and not in_string:
            break
        out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _parse_type(text: str) -> Any:
    from .ir import IRType

    return IRType(text.strip())


def _split_operands(text: str) -> list[str]:
    """Split on commas that are not inside a quoted string."""
    parts: list[str] = []
    depth_quote = False
    current: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            depth_quote = not depth_quote
        if ch == "," and not depth_quote:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def _parse_operand(text: str, values: "dict[str, Any]") -> Any:
    from .ir import I64, IRValue

    text = text.strip()
    if text.startswith("%"):
        name = text[1:]
        # Forward reference (a back-edge use) gets a placeholder that the
        # second pass repoints once the definition has been seen.
        return values.setdefault(name, IRValue(name, I64))
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        import ast as _ast

        return _ast.literal_eval(text)
    try:
        return int(text, 0)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text                      # a label or a symbol name


def parse_module(text: str) -> IRModule:
    """Read a :func:`format_module` dump back into an ``IRModule``."""
    from .ir import IRBlock, IRFunc, IRGlobal, IRInstr, IRValue, Visibility

    module = IRModule()
    values: dict[str, IRValue] = {}
    func: IRFunc | None = None
    block: IRBlock | None = None

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw).strip()
        if not line:
            continue

        if line.startswith("exports "):
            module.exports.extend(
                n.strip() for n in line[len("exports "):].split(",") if n.strip())
            continue

        if line.startswith("@"):
            head, _, value_text = line[1:].partition("=")
            name, _, type_text = head.strip().partition(":")
            tls = "tls" in type_text
            type_text = type_text.replace("tls", "").strip()
            value = None
            if value_text.strip():
                import ast as _ast

                value = _ast.literal_eval(value_text.strip())
            module.data.append(
                IRGlobal(name.strip(), _parse_type(type_text), value, tls))
            continue

        if line == "}":
            func = None
            block = None
            continue

        if line.startswith("func ") or " func " in line:
            vis = Visibility.UNDEFINED
            if not line.startswith("func "):
                keyword, _, line = line.partition(" ")
                vis = Visibility(keyword)
            body = line[len("func "):].rstrip("{").strip()
            name, _, rest = body.partition("(")
            params_text, _, ret_text = rest.partition(")")
            params: list[IRValue] = []
            for part in _split_operands(params_text):
                pname, _, ptype = part.partition(":")
                pname = pname.strip()
                # Exactly one sigil: ir_lower's own value names already
                # start with "%", so lstrip("%") eats both and the name
                # round-trips as a different value.
                value = IRValue(pname[1:] if pname.startswith("%") else pname,
                                _parse_type(ptype))
                values[value.name] = value
                params.append(value)
            ret_text = ret_text.replace("->", "").strip()
            ret = None if ret_text in ("", "none", "void") else _parse_type(ret_text)
            func = IRFunc(name.strip(), params, ret, [], vis)
            # `values` maps a name to the ONE IRValue object every mention
            # shares, so a forward reference can be repointed once its
            # definition is seen. Value names are function-scoped: without
            # this reset, a later function defining %t3 mutates the type of an
            # earlier function's %t3, and the two silently swap.
            values = {p.name: p for p in params}
            module.funcs.append(func)
            continue

        if func is None:
            raise IRParseError(f"line {lineno}: text outside a function: {raw!r}")

        if line.endswith(":") and " " not in line[:-1]:
            block = IRBlock(line[:-1])
            func.blocks.append(block)
            continue

        if block is None:
            raise IRParseError(f"line {lineno}: instruction outside a block: {raw!r}")

        result = None
        if line.startswith("%"):
            target, _, line = line.partition("=")
            rname, _, rtype = target.strip().partition(":")
            rname = rname.strip()
            rname = rname[1:] if rname.startswith("%") else rname
            result = values.get(rname)
            if result is None:
                result = IRValue(rname, _parse_type(rtype))
                values[rname] = result
            else:
                result.type = _parse_type(rtype)   # repoint a forward reference
            line = line.strip()

        op, _, operand_text = line.partition(" ")
        operands = [_parse_operand(p, values) for p in _split_operands(operand_text)]
        block.instrs.append(IRInstr(op.strip(), result, operands))

    return module


def emit_if_requested(module: IRModule, *, abi: str = "sysv") -> None:
    """Honour ``ASMPYTHON_EMIT_IR`` / ``ASMPYTHON_EMIT_ALLOC``. No-op if unset.

    Deliberately an environment variable rather than a CLI flag: it matches
    ``ASMPYTHON_VERIFY_IR`` next to it in the driver, and it works from inside
    a test or a differential harness without threading a parameter through
    every build entry point.
    """
    target = os.environ.get("ASMPYTHON_EMIT_IR")
    if not target:
        return
    text = format_module(
        module,
        with_alloc=bool(os.environ.get("ASMPYTHON_EMIT_ALLOC")),
        abi=abi,
    )
    if target == "-":
        import sys

        print(text, file=sys.stderr)
        return
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(text)


__all__ = ["emit_if_requested", "format_func", "format_module"]

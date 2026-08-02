"""The textual IR: printing and parsing.

The text form is the IR's user interface. It is what `irc build --emit-ir`
writes, what a backend author reads while debugging, and -- because it parses
back -- what you can hand-write to test a backend without running a frontend at
all. That last use is the reason the parser exists: writing twenty lines of IR
by hand is a far better first test of a new backend than compiling a Python
program and hoping.

    module demo

    global greeting = "hi\00"          ; 3 bytes, readonly

    func sum_to(%0: i64) -> i64 {
    entry:
        %1 = i64.const 0
        %2 = i64.copy %1
        jump loop
    loop:
        %3 = i64.lt %2, %0             ; ty is the OPERAND type; result is i1
        branch %3, body, done
    body:
        %2 = i64.add %2, %1            ; a register may be reassigned
        jump loop
    done:
        ret %2
    }

`<ty>.<op>` reads as "do `op` at width `ty`". For a comparison that is the type
being compared, not the result -- comparisons always produce i1, so printing
`i1.lt` would tell you the one thing you already knew and hide the one thing you
need.

Round-tripping is a test, not a nicety: `parse(print(m))` must equal `m`. It is
checked in `tests/test_text.py`, and it is what lets the printer be trusted as a
debugging tool -- a printer that quietly loses a field produces output that
looks right and misleads for hours.
"""
from __future__ import annotations

import re

from ..diagnostics import is_real
from . import opcodes as ops, types as T
from .module import Block, Function, Global, Instruction, Linkage, Module
from .opcodes import Op

_INDENT = "    "


# ══════════════════════════════════════════════════════════════ printing ════
def print_module(m: Module, show_spans: bool = False) -> str:
    out: list[str] = [f"module {m.name}", ""]
    for g in m.globals:
        out.append(_global(g))
    if m.globals:
        out.append("")
    for f in m.functions:
        out.append(print_func(f, show_spans))
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


def _global(g: Global) -> str:
    flags = "".join(x for x in (" readonly" if g.readonly else "",
                                " export" if g.linkage is Linkage.EXPORT else ""))
    if g.data is None:
        return f"global {g.name}: {g.size} bytes{flags}"
    return f"global {g.name} = {_bytes_lit(g.data)}{flags}"


def _bytes_lit(b: bytes) -> str:
    out = ['"']
    for ch in b:
        if ch == 0x22:
            out.append('\\"')
        elif ch == 0x5C:
            out.append("\\\\")
        elif 0x20 <= ch < 0x7F:
            out.append(chr(ch))
        else:
            out.append(f"\\{ch:02x}")
    out.append('"')
    return "".join(out)


def print_func(f: Function, show_spans: bool = False) -> str:
    params = ", ".join(f"%{p}: {f.registers.get(p, T.VOID)}" for p in f.params)
    head = f"func {f.name}({params}) -> {f.ret}"
    if f.linkage is Linkage.EXPORT:
        head = "export " + head
    if f.external:
        return head + " external"
    lines = [head + " {"]
    for b in f.blocks:
        lines.append(f"{b.label}:")
        for ins in b.instructions:
            lines.append(_INDENT + print_instr(ins, show_spans))
    lines.append("}")
    return "\n".join(lines)


def print_instr(ins: Instruction, show_spans: bool = False) -> str:
    name = ins.op.value
    mnemonic = name if ins.ty.is_void else f"{ins.ty}.{name}"
    args = ", ".join(f"%{a}" for a in ins.args)

    if ins.op is Op.CONST:
        body = f"{mnemonic} {_imm(ins.imm)}"
    elif ins.op is Op.ALLOCA:
        body = f"{mnemonic} {ins.imm}"
    elif ins.op in (Op.GLOBAL_ADDR, Op.FUNC_ADDR):
        body = f"{mnemonic} @{ins.sym}"
    elif ins.op is Op.CALL:
        body = f"{mnemonic} @{ins.sym}({args})"
    elif ins.op is Op.CALL_PTR:
        first, rest = (ins.args[0], ins.args[1:]) if ins.args else (None, [])
        inner = ", ".join(f"%{a}" for a in rest)
        body = f"{mnemonic} %{first}({inner})"
    elif ins.op is Op.JUMP:
        body = f"{name} {ins.labels[0] if ins.labels else '?'}"
    elif ins.op is Op.BRANCH:
        tgt = ", ".join(ins.labels)
        body = f"{name} {args}, {tgt}"
    elif ins.op is Op.SWITCH:
        cases = " ".join(f"[{v} -> {l}]" for v, l in ins.cases)
        dflt = ins.labels[0] if ins.labels else "?"
        body = f"{mnemonic} {args}, default {dflt} {cases}".rstrip()
    elif ins.op is Op.RET:
        body = f"{name} {args}".rstrip()
    elif ins.op is Op.UNREACHABLE:
        body = name
    else:
        body = f"{mnemonic} {args}".rstrip()

    line = f"%{ins.dst} = {body}" if ins.dst is not None else body
    if show_spans and is_real(ins.span):
        loc = ins.span.start_loc
        line += f"    ; {ins.span.file.name}:{loc.line}:{loc.column}"
    return line


def _imm(v) -> str:
    if isinstance(v, float):
        # repr() round-trips a float exactly; str() does not for every value.
        return repr(v)
    return str(v)


# ═══════════════════════════════════════════════════════════════ parsing ════
class ParseError(Exception):
    def __init__(self, line_no: int, line: str, why: str) -> None:
        super().__init__(f"line {line_no}: {why}\n    {line.strip()}")


_RE_REG = re.compile(r"%(\d+)")
_RE_FUNC = re.compile(
    r"^(export\s+)?func\s+([A-Za-z_.$][\w.$]*)\s*\((.*?)\)\s*->\s*(\w+)\s*(\{|external)$"
)
_RE_GLOBAL = re.compile(r"^global\s+([A-Za-z_.$][\w.$]*)\s*(?::\s*(\d+)\s*bytes|=\s*(\".*\"))(.*)$")
_RE_BLOCK = re.compile(r"^([A-Za-z_.$][\w.$]*):$")


def parse_module(text: str) -> Module:
    """Parse the textual form. Raises ParseError with the offending line."""
    m = Module()
    fn: Function | None = None
    blk: Block | None = None

    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split(";")[0].strip() if not _in_string_semicolon(raw) else raw.strip()
        if not line:
            continue

        # ONE guard for the whole line, not one per parse site. The
        # parser is full of `int(token[1:])`, `parts[1]` and
        # `T.parse(word)`, and each is a separate way for corrupt text to
        # raise something that is not a ParseError -- which reaches
        # `asmpython run` as a traceback. Mutating a valid module 18000
        # times found two such escapes; wrapping each site as it turned up
        # would have left the third.
        try:
            if line.startswith("module "):
                m.name = line[7:].strip()
                continue

            g = _RE_GLOBAL.match(line)
            if g and fn is None:
                name, size, lit, flags = g.groups()
                data = _parse_bytes(lit) if lit else None
                m.globals.append(Global(
                    name=name,
                    size=len(data) if data is not None else int(size),
                    data=data,
                    readonly="readonly" in (flags or ""),
                    linkage=(Linkage.EXPORT if "export" in (flags or "")
                             else Linkage.INTERNAL),
                ))
                continue

            f = _RE_FUNC.match(line)
            if f:
                exported, name, params, ret, tail = f.groups()
                fn = Function(
                    name, T.parse(ret),
                    linkage=Linkage.EXPORT if exported else Linkage.INTERNAL,
                    external=(tail == "external"))
                m.functions.append(fn)
                blk = None
                for p in [x.strip() for x in params.split(",") if x.strip()]:
                    mreg = re.match(r"%(\d+)\s*:\s*(\w+)$", p)
                    if not mreg:
                        raise ParseError(n, raw, f"bad parameter {p!r}")
                    reg, ty = int(mreg.group(1)), T.parse(mreg.group(2))
                    fn.params.append(reg)
                    fn.registers[reg] = ty
                continue

            if line == "}":
                fn, blk = None, None
                continue

            if fn is None:
                raise ParseError(n, raw, "statement outside a function")

            b = _RE_BLOCK.match(line)
            if b:
                blk = Block(b.group(1))
                fn.blocks.append(blk)
                continue

            if blk is None:
                raise ParseError(n, raw, "instruction before any block label")

            blk.instructions.append(_parse_instr(n, raw, line, fn))
        except ParseError:
            raise
        except (ValueError, IndexError, KeyError, AttributeError) as exc:
            raise ParseError(n, raw,
                             f"{type(exc).__name__}: {exc}") from None

    return m


def _in_string_semicolon(raw: str) -> bool:
    """True if a ';' appears inside a string literal on this line.

    Without this, `global s = "a;b"` loses everything after the semicolon and
    the byte count silently changes -- a data corruption that looks like a
    frontend bug.
    """
    if ";" not in raw or '"' not in raw:
        return False
    return raw.index('"') < raw.index(";")


def _parse_bytes(lit: str) -> bytes:
    body = lit[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        c = body[i]
        if c == "\\":
            nxt = body[i + 1]
            if nxt == "\\":
                out.append(0x5C); i += 2
            elif nxt == '"':
                out.append(0x22); i += 2
            else:
                out.append(int(body[i + 1:i + 3], 16)); i += 3
        else:
            out.extend(c.encode("utf-8")); i += 1
    return bytes(out)


def _parse_instr(n: int, raw: str, line: str, fn: Function) -> Instruction:
    dst = None
    if "=" in line and line.split("=")[0].strip().startswith("%"):
        lhs, line = line.split("=", 1)
        dst = int(lhs.strip()[1:])
        line = line.strip()

    head, _, rest = line.partition(" ")
    rest = rest.strip()
    if "." in head:
        ty_name, _, op_name = head.partition(".")
        ty = T.parse(ty_name)
    else:
        ty, op_name = T.VOID, head
    try:
        op = ops.by_name(op_name)
    except ValueError as e:
        raise ParseError(n, raw, str(e)) from None

    # Some opcodes have a `ty` that is fully determined, so the text omits it
    # for readability -- `branch %5, a, b` rather than `i1.branch ...`, and
    # `ret %1` rather than `i64.ret %1`. Restore it here so the in-memory form
    # is uniform and the verifier still has something to check against.
    if op is Op.BRANCH:
        ty = T.I1
    elif op is Op.RET:
        ty = fn.ret

    ins = Instruction(op, ty, dst=dst)

    if op is Op.CONST:
        ins.imm = float(rest) if ("." in rest or "e" in rest.lower()) else int(rest)
    elif op is Op.ALLOCA:
        ins.imm = int(rest)
    elif op in (Op.GLOBAL_ADDR, Op.FUNC_ADDR):
        ins.sym = rest.lstrip("@")
    elif op is Op.CALL:
        sym, _, argstr = rest.partition("(")
        ins.sym = sym.strip().lstrip("@")
        ins.args = [int(x) for x in _RE_REG.findall(argstr)]
    elif op is Op.CALL_PTR:
        target, _, argstr = rest.partition("(")
        ins.args = [int(x) for x in _RE_REG.findall(target)]
        ins.args += [int(x) for x in _RE_REG.findall(argstr)]
    elif op is Op.JUMP:
        ins.labels = [rest]
    elif op is Op.BRANCH:
        parts = [p.strip() for p in rest.split(",")]
        ins.args = [int(parts[0][1:])]
        ins.labels = parts[1:]
    elif op is Op.SWITCH:
        cond, _, tail = rest.partition(",")
        ins.args = [int(cond.strip()[1:])]
        dm = re.search(r"default\s+(\S+)", tail)
        ins.labels = [dm.group(1)] if dm else []
        ins.cases = [(int(v), l) for v, l in re.findall(r"\[(-?\d+)\s*->\s*(\S+?)\]", tail)]
    else:
        ins.args = [int(x) for x in _RE_REG.findall(rest)]

    # A register's type is declared by whatever defines it; parameters were
    # declared by the signature. Reading a register never declares it, which is
    # what lets the verifier still catch a use with no definition.
    if dst is not None and dst not in fn.registers:
        fn.registers[dst] = T.I1 if ops.spec(op).result == "i1" else ty
    return ins

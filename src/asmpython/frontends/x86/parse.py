"""AT&T x86-64 assembly text to statements.

Assembly looks trivial to parse until you write one. The cases that bite, all
of them present in output GCC and asmpython both produce:

  * `.def add; .scl 2; .type 32; .endef` -- four directives on one line.
  * `movq $-8, -16(%rbp,%rax,8)` -- a negative immediate and a five-part
    memory operand on the same line, and the `-` means different things.
  * `.asciz "a, b"` -- a comma inside a string is not a separator, and
    splitting arguments on commas first loses it.
  * `1:` / `jmp 1b` -- numeric local labels, where `1b` means "the nearest
    `1:` BACKWARD" and is not a symbol named `1b`.
  * `movq %rax, %rbx # set up` -- comments start with `#`, but `#` also
    appears inside strings.

The parser is deliberately permissive about instructions it does not know:
an unrecognised mnemonic parses fine and becomes an `Instr`. Refusing here
would mean the parser holding a list of instructions, which is the lifter's
business -- and the resulting diagnostic would say "syntax error" for
something that is spelled perfectly.
"""
from __future__ import annotations

import re

from ...diagnostics import DiagnosticSink, SourceFile, Span, error
from .syntax import Directive, Imm, Instr, LabelDef, Mem, Reg, Statement, Sym
from .syntax import REGISTERS

#: `1:` defines a local label; `1f`/`1b` reference the next/previous one.
_RE_LOCAL_REF = re.compile(r"^(\d+)([bf])$")
_RE_LABEL = re.compile(r"^([.\w$@]+)\s*:(.*)$")
_RE_INT = re.compile(r"^[+-]?(0[xX][0-9a-fA-F]+|\d+)$")


class ParseError(Exception):
    """Raised only for text that cannot be turned into statements at all."""


def parse(source: SourceFile, sink: DiagnosticSink) -> list[Statement] | None:
    """Parse a whole file. Reports to `sink` and returns None on any error."""
    p = Parser(source, sink)
    statements = p.run()
    return None if sink.failed else statements


class Parser:
    def __init__(self, source: SourceFile, sink: DiagnosticSink) -> None:
        self.source = source
        self.sink = sink
        #: Byte offset of the line being parsed, and the line's raw text.
        #: A span is a byte RANGE, so a diagnostic that wants to underline an
        #: operand has to locate it in the line rather than count columns.
        self.line_start = 0
        self.raw = ""

    # -- diagnostics -------------------------------------------------------
    def _span(self, text: str = "") -> Span:
        found = self.raw.find(text) if text else -1
        if found < 0:
            return Span(self.source, self.line_start,
                        self.line_start + max(len(self.raw), 1))
        start = self.line_start + found
        return Span(self.source, start, start + max(len(text), 1))

    def _error(self, code: str, message: str, text: str = "", *,
               help: str = "") -> None:
        d = error(code, message).at(self._span(text))
        if help:
            d.help(help)
        self.sink.report(d)

    # -- driver ------------------------------------------------------------
    def run(self) -> list[Statement]:
        out: list[Statement] = []
        offset = 0
        for raw in self.source.text.split("\n"):
            self.line_start, self.raw = offset, raw
            offset += len(raw) + 1
            for piece in self._split_statements(strip_comment(raw)):
                stmt = self._statement(piece)
                if stmt is None:
                    continue
                # `main: ret` is one line and two statements; _Pair is a list
                # so this flattens without a type test.
                out.extend(stmt) if isinstance(stmt, _Pair) else out.append(stmt)
        return out

    def _split_statements(self, text: str) -> list[str]:
        """One line can hold several statements, separated by `;`.

        Splitting has to respect strings and labels: `.asciz "a;b"` is one
        directive, and a `;` inside it is data.
        """
        pieces, current, quote = [], [], ""
        for ch in text:
            if quote:
                current.append(ch)
                if ch == quote and (len(current) < 2 or current[-2] != "\\"):
                    quote = ""
                continue
            if ch in "\"'":
                quote = ch
                current.append(ch)
            elif ch == ";":
                pieces.append("".join(current))
                current = []
            else:
                current.append(ch)
        pieces.append("".join(current))
        return [p.strip() for p in pieces if p.strip()]

    def _statement(self, text: str) -> Statement | None:
        # A label may be followed on the same line by an instruction, and
        # `foo: bar: nop` is legal -- so this loops rather than matching once.
        match = _RE_LABEL.match(text)
        if match and not _is_operand_colon(text, match):
            rest = match.group(2).strip()
            label = LabelDef(match.group(1), self._span(text))
            if rest:
                trailing = self._statement(rest)
                if trailing is not None:
                    return _Pair(label, *(trailing if isinstance(trailing, _Pair)
                                          else (trailing,)))
            return label

        if text.startswith("."):
            return self._directive(text)
        return self._instruction(text)

    def _directive(self, text: str) -> Directive:
        name, rest = _head(text)
        return Directive(name.lstrip("."), split_args(rest),
                         self._span(text))

    def _instruction(self, text: str) -> Instr | None:
        mnemonic, rest = _head(text)
        if not mnemonic:
            return None
        operands = []
        for piece in split_args(rest):
            operand = self._operand(piece)
            if operand is None:
                return None
            operands.append(operand)
        return Instr(mnemonic.lower(), operands, self._span(text))

    # -- operands ----------------------------------------------------------
    def _operand(self, text: str):
        text = text.strip()
        if not text:
            return None
        if text.startswith("$"):
            return self._immediate(text[1:])
        if text.startswith("%"):
            return self._register(text[1:])
        if text.startswith("*"):
            # `call *%rax` / `jmp *8(%rbx)`: an indirect target. The star is
            # not part of the operand, it is the addressing MODE, and the
            # lifter distinguishes call-indirect by the operand's kind.
            return self._operand(text[1:])
        if "(" in text or text.startswith("-") or _RE_INT.match(text):
            return self._memory(text)
        return Sym(text)

    def _immediate(self, text: str) -> Imm:
        if _RE_INT.match(text):
            return Imm(int(text, 0))
        return Imm(None, symbol=text)

    def _register(self, text: str) -> Reg | None:
        try:
            canon, bits = REGISTERS[text.lower()]
        except KeyError:
            self._error("E0700", f"unknown register %{text}", text,
                        help="x86-64 register names are like %rax, %r10d, "
                             "%xmm0")
            return None
        return Reg(canon, bits, text.lower())

    def _memory(self, text: str) -> Mem | None:
        """`disp(base,index,scale)`, `sym(%rip)`, `sym`, or a bare number."""
        head, _, inner = text.partition("(")
        head = head.strip()
        if not inner:
            # No parentheses: an absolute address or a symbol reference.
            if _RE_INT.match(head):
                return Mem(disp=int(head, 0))
            return Mem(symbol=head)
        if not inner.endswith(")"):
            self._error("E0701", "unclosed '(' in memory operand", text)
            return None
        parts = [p.strip() for p in inner[:-1].split(",")]

        disp, symbol = 0, None
        if head:
            if _RE_INT.match(head):
                disp = int(head, 0)
            else:
                symbol = head

        def reg_name(piece: str) -> str | None:
            if not piece:
                return None
            if not piece.startswith("%"):
                self._error("E0702",
                            f"expected a register in a memory operand, "
                            f"got {piece!r}", text)
                return None
            reg = self._register(piece[1:])
            return reg.name if reg else None

        base = reg_name(parts[0]) if parts and parts[0] else None
        index = reg_name(parts[1]) if len(parts) > 1 and parts[1] else None
        scale = 1
        if len(parts) > 2 and parts[2]:
            if not _RE_INT.match(parts[2]):
                self._error("E0703", f"scale must be a literal, got "
                                     f"{parts[2]!r}", text)
                return None
            scale = int(parts[2], 0)
            if scale not in (1, 2, 4, 8):
                self._error("E0704", f"scale must be 1, 2, 4 or 8, not "
                                     f"{scale}", text)
                return None
        if len(parts) > 3:
            self._error("E0705", "a memory operand has at most three parts: "
                                 "disp(base, index, scale)", text)
            return None
        return Mem(disp=disp, base=base, index=index, scale=scale,
                   symbol=symbol, rip_relative=(base == "rip"))


class _Pair(list):
    """A label and the statement that shared its line.

    A list subclass so `run()` can flatten it without a type test, and named
    so the reason survives: `main: ret` is two statements and one line, and
    dropping either loses a function or its body.
    """

    def __init__(self, *items: Statement) -> None:
        super().__init__(items)


def _head(text: str) -> tuple[str, str]:
    """Split a statement into its first word and the rest.

    GCC separates a mnemonic from its operands with a TAB, asmpython with a
    space, and both are ordinary whitespace to an assembler. Splitting on
    " " alone swallowed every GCC operand into the mnemonic and lower-cased
    it, which turned `je .L7` into an instruction named `je	.l7` -- and it
    still parsed, so nothing complained until the mnemonics were printed.
    """
    parts = text.split(None, 1)
    if not parts:
        return "", ""
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def strip_comment(line: str) -> str:
    """Remove a `#` or `//` comment, respecting string literals."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote and (i == 0 or line[i - 1] != "\\"):
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i]
        elif ch == "/" and line[i:i + 2] == "//":
            return line[:i]
    return line


def split_args(text: str) -> list[str]:
    """Split on commas that are not inside parentheses or a string.

    `movq $1, -8(%rbp,%rax,8)` has four commas and two operands. Splitting on
    every comma is the single most common way to get this wrong, and it fails
    silently -- the memory operand becomes three unparseable fragments.
    """
    args, current, depth, quote = [], [], 0, ""
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote and (len(current) < 2 or current[-2] != "\\"):
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(current))
            current = []
        else:
            current.append(ch)
    args.append("".join(current))
    return [a.strip() for a in args if a.strip()]


def _is_operand_colon(text: str, match: re.Match) -> bool:
    """True when the `:` found is a segment override, not a label.

    `%fs:0x28` is a segment-prefixed address and contains a colon in a
    position that looks exactly like a label to a regular expression.
    """
    return text.lstrip().startswith("%")

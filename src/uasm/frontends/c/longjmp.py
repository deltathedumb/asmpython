"""`setjmp` and `longjmp`, without a machine frame.

WHAT MAKES THIS HARD, and it is worth saying before the answer. `longjmp`
restores a saved stack pointer and program counter: it throws away every
frame between the `longjmp` and the `setjmp` without those frames knowing.
The UIR has no way to name a frame -- virtual registers, basic blocks and
branches within one function, and nothing about a stack -- and that absence
is exactly what lets the same IR run in an interpreter, as JVM bytecode, as a
`.pyc` and as machine code without any of them agreeing about what a stack
is. Adding a frame-restoring opcode would take that away from every backend
to serve one header.

SO THE FRAMES UNWIND THEMSELVES. `longjmp` sets a flag; every call site in
the program checks it the moment the call returns and, if it is set, returns
immediately; the frame that recognises the token branches back to its own
`setjmp`. That is how a language without exceptions implements exceptions,
and it is what a compiler does for a target with no unwinder. It costs a load
and a branch per call -- in a program that uses `setjmp`, and in no other.

THE TOKEN IS THE ACTIVATION, not the function and not the `jmp_buf`. A
counter hands out one per `setjmp` call, the `jmp_buf` holds it, and the
frame holds it in a slot of its own; recursion therefore works, because each
activation of the function that called `setjmp` has a different one. A
`longjmp` to a frame that has already returned matches nothing and unwinds
out of the program, which is what C calls undefined and is the best an
implementation with no stack to inspect can do.

WHAT C PROMISES AND WHAT THIS GIVES.

  * `setjmp` answers 0 the first time and `longjmp`'s value (or 1) after one.
  * A local that is not `volatile` and was changed between the two has an
    INDETERMINATE value, says C. Here it keeps the value it had: the frame
    never went away, so its registers are exactly as it left them. Stricter
    than the standard, which is the safe direction.
  * `longjmp` from a signal handler, or to a frame that has returned, is
    undefined. It is undefined here too.
  * C lists four contexts a `setjmp` call may appear in. This does not
    enforce them: the result is an ordinary value in an ordinary register, so
    a use C forbids works rather than breaking.
  * Taking the ADDRESS of `setjmp` is refused, because there is no function
    to point at -- it is compiled into a branch inside its caller.

WHERE IT RUNS. Over the whole module, after the translation units are merged:
every function between the `longjmp` and the `setjmp` needs the check,
including a library function like `qsort` that calls back into the program,
and the unit that defines it is not the one that mentioned `setjmp`.
"""
from __future__ import annotations

from ...diagnostics import NO_SPAN, DiagnosticSink, error
from ...ir import types as T
from ...ir.module import Block, Function, Global, Instruction, Linkage, Module
from ...ir.opcodes import Op

#: What `<setjmp.h>` calls them, which is NOT what C calls them: `setjmp` is
#: a macro, and a program may `#undef` it and take the address of a function
#: -- which this cannot implement, and would rather refuse than mis-compile.
SETJMP = "__c_setjmp"
LONGJMP = "__c_longjmp"

#: The four words the mechanism runs on. Module globals rather than anything
#: cleverer: the IR has no thread-local storage, and there is one thread.
ACTIVE = "__c_jmp_active"     # non-zero while a longjmp is unwinding
TARGET = "__c_jmp_target"     # the activation token it is looking for
VALUE = "__c_jmp_value"       # what the setjmp it reaches should answer
SEQ = "__c_jmp_seq"           # the counter that hands out tokens

WORD = T.I64


def used(module: Module) -> bool:
    """Whether anything in `module` calls `setjmp` or `longjmp`."""
    return any(ins.sym in (SETJMP, LONGJMP)
               for fn in module.functions if not fn.external
               for _, ins in fn.instructions())


def apply(module: Module, sink: DiagnosticSink) -> None:
    """Rewrite `module` so that a `longjmp` reaches its `setjmp`."""
    for fn in module.functions:
        if fn.external:
            continue
        for _, ins in fn.instructions():
            if ins.op is Op.FUNC_ADDR and ins.sym in (SETJMP, LONGJMP):
                sink.report(
                    error("E1603", "the address of `setjmp` cannot be taken")
                    .at(ins.span)
                    .note("it is compiled into a branch inside the function "
                          "that calls it, so there is no function to point at")
                    .help("call it directly; a program that does this has "
                          "undefined behaviour in C as well"))
                return
    for name in (ACTIVE, TARGET, VALUE, SEQ):
        if module.global_(name) is None:
            module.globals.append(Global(name, 8, None, linkage=Linkage.EXPORT))
    for fn in module.functions:
        if not fn.external and fn.blocks:
            _one_function(fn)
    # The two declarations have no calls left: every one is now inline.
    module.functions = [f for f in module.functions
                        if not (f.external and f.name in (SETJMP, LONGJMP))]


# ── writing instructions into a block that is being rebuilt ─────────────────
class _Emit:
    """Just enough builder for this pass.

    NOT `ir.builder.Builder`, which owns a function's block list and appends
    to it: this pass REPLACES that list as it goes, and two things appending
    to `fn.blocks` at once is how a block ends up in it twice.
    """

    def __init__(self, fn: Function, block: Block, span) -> None:
        self.fn = fn
        self.block = block
        self.span = span or NO_SPAN

    def emit(self, ins: Instruction) -> int | None:
        ins.span = self.span
        self.block.instructions.append(ins)
        return ins.dst

    def reg(self, ty) -> int:
        return self.fn.new_register(ty)

    def const(self, ty, value) -> int:
        return self.emit(Instruction(Op.CONST, ty, dst=self.reg(ty),
                                     imm=value))

    def load(self, ty, addr: int) -> int:
        return self.emit(Instruction(Op.LOAD, ty, dst=self.reg(ty),
                                     args=[addr]))

    def store(self, ty, value: int, addr: int) -> None:
        self.emit(Instruction(Op.STORE, ty, args=[value, addr]))

    def global_addr(self, name: str) -> int:
        return self.emit(Instruction(Op.GLOBAL_ADDR, T.PTR, dst=self.reg(T.PTR),
                                     sym=name))

    def load_global(self, name: str) -> int:
        return self.load(WORD, self.global_addr(name))

    def store_global(self, name: str, value: int) -> None:
        self.store(WORD, value, self.global_addr(name))

    def cmp(self, op: Op, ty, a: int, b: int) -> int:
        return self.emit(Instruction(op, ty, dst=self.reg(T.I1), args=[a, b]))

    def widen(self, value: int) -> int:
        """`value` as a word. The mechanism's own arithmetic is all i64."""
        have = self.fn.register_type(value)
        if have is WORD:
            return value
        op = Op.EXTEND if have.bits < WORD.bits else Op.TRUNC
        return self.emit(Instruction(op, WORD, dst=self.reg(WORD),
                                     args=[value]))

    def branch(self, cond: int, then: str, els: str) -> None:
        self.emit(Instruction(Op.BRANCH, T.I1, args=[cond], labels=[then, els]))

    def jump(self, label: str) -> None:
        self.emit(Instruction(Op.JUMP, T.VOID, labels=[label]))

    def ret_zero(self) -> None:
        """Return whatever the signature needs. THE VALUE IS NEVER SEEN: the
        caller's own check fires the instant this returns, and it returns
        too, up to the frame that catches the jump."""
        if self.fn.ret.is_void:
            self.emit(Instruction(Op.RET, T.VOID))
        else:
            self.emit(Instruction(Op.RET, self.fn.ret,
                                  args=[self.const(self.fn.ret, 0)]))


def _block(fn: Function, taken: set[str], hint: str) -> Block:
    """A block with a label nothing else has. Not added to the function: the
    caller is rebuilding that list and decides where it goes."""
    n = 0
    while f"{hint}{n}" in taken:
        n += 1
    taken.add(f"{hint}{n}")
    return Block(f"{hint}{n}")


# ── the pass, per function ──────────────────────────────────────────────────
def _one_function(fn: Function) -> None:
    taken = {b.label for b in fn.blocks}
    sites = _collect(fn, taken)
    if sites:
        _prologue(fn, sites)
    blocks = _rewrite(fn, sites, taken)
    fn.blocks = _checks(fn, blocks, sites, taken)
    if sites:
        _zero_registers(fn)


def _collect(fn: Function, taken: set[str]):
    """One slot and one resume block per `setjmp` call, named before anything
    is rewritten -- the code for a `longjmp` needs the whole list, and a
    `longjmp` may come first in the instruction stream."""
    sites = []
    for _, ins in fn.instructions():
        if ins.op is Op.CALL and ins.sym == SETJMP:
            sites.append({
                "ins": id(ins),
                "slot": fn.new_register(T.PTR),
                "resume": _block(fn, taken, "sj.resume"),
                "span": ins.span,
            })
    return sites


def _prologue(fn: Function, sites) -> None:
    """The token slots, allocated and zeroed in the entry block.

    IN THE ENTRY BLOCK AND NOWHERE ELSE, for two reasons that both matter.
    The unwind check reads every slot, and it runs at call sites that the
    `setjmp` does not dominate -- a slot allocated where the `setjmp` is
    would be an address that had never been computed. And zero is what makes
    such a check harmless: tokens start at 1, so a frame whose `setjmp` has
    not run yet matches nothing.
    """
    entry = fn.blocks[0]
    span = entry.instructions[0].span if entry.instructions else NO_SPAN
    head = Block("<prologue>")
    e = _Emit(fn, head, span)
    for site in sites:
        e.emit(Instruction(Op.ALLOCA, T.PTR, dst=site["slot"], imm=8))
        e.store(WORD, e.const(WORD, 0), site["slot"])
    entry.instructions[:0] = head.instructions


def _rewrite(fn: Function, sites, taken: set[str]) -> list[Block]:
    """Replace the calls to `__c_setjmp` and `__c_longjmp` with their code."""
    if not sites and not any(ins.sym == LONGJMP
                             for _, ins in fn.instructions()):
        return list(fn.blocks)
    by_ins = {site["ins"]: site for site in sites}
    out: list[Block] = []
    for blk in fn.blocks:
        cur = Block(blk.label)
        out.append(cur)
        for ins in blk.instructions:
            site = by_ins.get(id(ins)) if ins.op is Op.CALL else None
            if site is not None and ins.sym == SETJMP:
                after = _block(fn, taken, "sj.after")
                e = _Emit(fn, cur, ins.span)
                # A FRESH TOKEN. The counter starts at 1 so that an
                # untouched slot -- zero -- is not anybody's.
                tok = e.emit(Instruction(
                    Op.ADD, WORD, dst=e.reg(WORD),
                    args=[e.load_global(SEQ), e.const(WORD, 1)]))
                e.store_global(SEQ, tok)
                e.store(WORD, tok, site["slot"])
                # AND IN THE `jmp_buf`, which is what `longjmp` reads.
                e.store(WORD, tok, ins.args[0])
                if ins.dst is not None:
                    e.emit(Instruction(Op.CONST, ins.ty, dst=ins.dst, imm=0))
                e.jump(after.label)
                # The way back in: the same register, the other value.
                r = _Emit(fn, site["resume"], ins.span)
                if ins.dst is not None:
                    v = r.load_global(VALUE)
                    op = (Op.COPY if ins.ty is WORD
                          else Op.TRUNC if ins.ty.bits < WORD.bits
                          else Op.EXTEND)
                    r.emit(Instruction(op, ins.ty, dst=ins.dst, args=[v]))
                r.jump(after.label)
                out.append(site["resume"])
                out.append(after)
                cur = after
                continue
            if ins.op is Op.CALL and ins.sym == LONGJMP:
                e = _Emit(fn, cur, ins.span)
                e.store_global(TARGET, e.load(WORD, ins.args[0]))
                # `longjmp(env, 0)` ANSWERS 1, which C requires: 0 is how a
                # `setjmp` says it is the first time through, so it must not
                # be sayable twice.
                val = e.widen(ins.args[1])
                chosen = e.reg(WORD)
                zero = _block(fn, taken, "sj.zero")
                keep = _block(fn, taken, "sj.keep")
                join = _block(fn, taken, "sj.join")
                e.branch(e.cmp(Op.EQ, WORD, val, e.const(WORD, 0)),
                         zero.label, keep.label)
                z = _Emit(fn, zero, ins.span)
                z.emit(Instruction(Op.CONST, WORD, dst=chosen, imm=1))
                z.jump(join.label)
                k = _Emit(fn, keep, ins.span)
                k.emit(Instruction(Op.COPY, WORD, dst=chosen, args=[val]))
                k.jump(join.label)
                j = _Emit(fn, join, ins.span)
                j.store_global(VALUE, chosen)
                j.store_global(ACTIVE, j.const(WORD, 1))
                out.extend((zero, keep, join))
                # NO `ret` HERE, and no special case. The check that follows
                # every call goes after this too -- it sees the flag that was
                # just set and either returns or, when this frame is the one
                # the jump names, resumes here. `longjmp` to a `setjmp` in
                # the SAME function is legal C and this is what makes it work.
                cur = _finish_with_check(fn, out, join, sites, taken,
                                         ins.span)
                continue
            cur.instructions.append(ins)
    return out


def _finish_with_check(fn: Function, out: list[Block], cur: Block, sites,
                       taken: set[str], span) -> Block:
    """Emit the unwind check at the end of `cur`; answer where to carry on.

    THE WHOLE MECHANISM IS THESE FIFTEEN INSTRUCTIONS. Is a jump unwinding?
    If not, carry on. If it is, is it looking for one of MY activations? If
    so, stop the unwinding and branch back to that `setjmp`. If not, return,
    and let the caller ask the same question.
    """
    tail = _block(fn, taken, "sj.tail")
    unwind = _block(fn, taken, "sj.unwind")
    e = _Emit(fn, cur, span)
    active = e.load_global(ACTIVE)
    e.branch(e.cmp(Op.NE, WORD, active, e.const(WORD, 0)),
             unwind.label, tail.label)
    out.append(unwind)
    u = _Emit(fn, unwind, span)
    if sites:
        target = u.load_global(TARGET)
        for site in sites:
            caught = _block(fn, taken, "sj.caught")
            other = _block(fn, taken, "sj.other")
            mine = u.load(WORD, site["slot"])
            u.branch(u.cmp(Op.EQ, WORD, target, mine),
                     caught.label, other.label)
            c = _Emit(fn, caught, span)
            c.store_global(ACTIVE, c.const(WORD, 0))
            c.jump(site["resume"].label)
            out.extend((caught, other))
            u = _Emit(fn, other, span)
    u.ret_zero()
    out.append(tail)
    return tail


def _checks(fn: Function, blocks: list[Block], sites,
            taken: set[str]) -> list[Block]:
    """After every call: leave the frame, or catch the jump."""
    out: list[Block] = []
    for blk in blocks:
        cur = Block(blk.label)
        out.append(cur)
        for i, ins in enumerate(blk.instructions):
            cur.instructions.append(ins)
            if ins.op not in (Op.CALL, Op.CALL_PTR):
                continue
            if i + 1 >= len(blk.instructions):
                # A CALL IS NOT A TERMINATOR, so this does not happen; if the
                # IR ever grows one that is, its check belongs in each
                # successor instead.
                continue
            cur = _finish_with_check(fn, out, cur, sites, taken, ins.span)
    return out


def _zero_registers(fn: Function) -> None:
    """Give every register a value before the function's first instruction.

    WHY, AND IT IS THE VERIFIER RATHER THAN THE MACHINE. Rule 3 is that a
    register is read after a write ON EVERY PATH that reaches the read, and
    the check adds an edge from every call site back to a `setjmp` that may
    sit inside an `if` -- so there is now a path to the code after the
    `setjmp` that did not run the code before it. At run time that cannot
    happen: a jump naming this frame's token means the `setjmp` really did
    execute. The verifier cannot know it and is right not to guess, so the
    registers are given a value it can see.

    ONLY IN A FUNCTION THAT CALLS `setjmp`. Everywhere else the check adds no
    edge into the middle of anything -- it falls through or it returns -- so
    nothing is read any earlier than it was before.
    """
    entry = fn.blocks[0]
    span = entry.instructions[0].span if entry.instructions else NO_SPAN
    params = set(fn.params)
    zeros = [Instruction(Op.CONST, ty, dst=reg, imm=0, span=span)
             for reg, ty in sorted(fn.registers.items())
             if reg not in params and not ty.is_void]
    entry.instructions[:0] = zeros

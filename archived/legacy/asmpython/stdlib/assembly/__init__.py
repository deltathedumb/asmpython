"""Public API for writing inline assembly that asmpython can compile and call.

    from asmpython.assembly import asm_func

`@asm_func`
-----------
Marks a function whose *body* is raw NASM x86-64. The Python signature carries
the contract the compiler needs — the symbol name, the parameter types, and the
return type — while the triple-quoted docstring carries the instructions that
are emitted verbatim as the function's body:

    @asm_func
    def add(a: int, b: int) -> int:
        \"\"\"
        ; args arrive per the target ABI (rdi/rsi on SysV, rcx/rdx on Win64)
        mov rax, rdi
        add rax, rsi
        ret
        \"\"\"

Ordinary asmpython code then calls `add(2, 3)` like any other function; the
compiler routes the call to the inline-asm body instead of generating one.

Under CPython, `@asm_func` returns a stub that raises if you call it — the
NASM body only means something to the asmpython compiler.
"""

from __future__ import annotations

from typing import Callable


__all__ = ["asm_func", "AssemblyFunc"]


class AssemblyFunc:
    """Marker wrapper the `@asm_func` decorator returns under CPython.

    Holds the NASM body and the declared symbol so introspection works, but
    raises on call: the body is machine code meant for the asmpython compiler,
    not something CPython can execute.
    """

    def __init__(self, fn: Callable, *, symbol: str | None = None) -> None:
        self._fn = fn
        self.name = fn.__name__
        self.symbol = symbol or fn.__name__
        # The raw NASM body lives in the function's docstring.
        self.asm = (fn.__doc__ or "").strip()
        # Preserve dunders so the stub still looks like the original function.
        self.__name__ = fn.__name__
        self.__doc__ = fn.__doc__
        self.__wrapped__ = fn

    def __call__(self, *args, **kwargs):
        raise RuntimeError(
            f"@asm_func {self.name!r} has a raw-NASM body and can only run "
            f"after compilation with asmpython; it is not callable under CPython."
        )

    def __repr__(self) -> str:
        return f"<asm_func {self.name!r} symbol={self.symbol!r}>"


class _AsmFuncDecorator:
    """Decorator returned by the parameterised form `@asm_func(symbol=...)`.

    A class (not a closure) so this module stays inside asmpython's compilable
    subset — nested `def` is interpreter-only glue the compiler doesn't model.
    """

    def __init__(self, symbol: str | None = None) -> None:
        self.symbol = symbol

    def __call__(self, real_fn: Callable) -> AssemblyFunc:
        return AssemblyFunc(real_fn, symbol=self.symbol)


class Assembly:
    """Composable NASM x86-64 source builder.

    Each instruction method appends one formatted line to `self.body`.
    When string-literal operands are passed the asmpython compiler validates
    them at compile time (unknown registers, empty operands) and raises a
    SemaError rather than silently emitting bad assembly.

    Call `execute()` to print the accumulated text to stdout.

    Python-keyword conflicts use a trailing underscore: ``and_``, ``or_``,
    ``not_``, ``in_``.  The three-operand imul form is ``imul3``.  The
    floating-point ``movsd`` is ``movsd_f`` (avoids clash with the string
    repeat prefix ``movsd``).
    """

    def __init__(self) -> None:
        self.body = ""

    def execute(self) -> None:
        """Print the accumulated NASM text to stdout."""
        print(self.body)

    # ------------------------------------------------------------------
    # Directives / structure
    # ------------------------------------------------------------------

    def raw(self, line: str) -> None:
        self.body += line + "\n"

    def comment(self, text: str) -> None:
        self.body += "; " + text + "\n"

    def label(self, name: str) -> None:
        self.body += name + ":\n"

    def section(self, name: str) -> None:
        self.body += "section " + name + "\n"

    def global_(self, symbol: str) -> None:
        self.body += "global " + symbol + "\n"

    def extern(self, symbol: str) -> None:
        self.body += "extern " + symbol + "\n"

    def bits(self, n: str) -> None:
        self.body += "bits " + n + "\n"

    def equ(self, name: str, value: str) -> None:
        self.body += name + " equ " + value + "\n"

    def db(self, name: str, value: str) -> None:
        self.body += name + ": db " + value + "\n"

    def dw(self, name: str, value: str) -> None:
        self.body += name + ": dw " + value + "\n"

    def dd(self, name: str, value: str) -> None:
        self.body += name + ": dd " + value + "\n"

    def dq(self, name: str, value: str) -> None:
        self.body += name + ": dq " + value + "\n"

    def resb(self, name: str, count: str) -> None:
        self.body += name + ": resb " + count + "\n"

    def resw(self, name: str, count: str) -> None:
        self.body += name + ": resw " + count + "\n"

    def resd(self, name: str, count: str) -> None:
        self.body += name + ": resd " + count + "\n"

    def resq(self, name: str, count: str) -> None:
        self.body += name + ": resq " + count + "\n"

    def times(self, count: str, directive: str) -> None:
        self.body += "times " + count + " " + directive + "\n"

    # ------------------------------------------------------------------
    # Data movement
    # ------------------------------------------------------------------

    def mov(self, dest: str, src: str) -> None:
        self.body += "    mov " + dest + ", " + src + "\n"

    def movzx(self, dest: str, src: str) -> None:
        self.body += "    movzx " + dest + ", " + src + "\n"

    def movsx(self, dest: str, src: str) -> None:
        self.body += "    movsx " + dest + ", " + src + "\n"

    def movsxd(self, dest: str, src: str) -> None:
        self.body += "    movsxd " + dest + ", " + src + "\n"

    def lea(self, dest: str, src: str) -> None:
        self.body += "    lea " + dest + ", " + src + "\n"

    def push(self, src: str) -> None:
        self.body += "    push " + src + "\n"

    def pop(self, dest: str) -> None:
        self.body += "    pop " + dest + "\n"

    def xchg(self, a: str, b: str) -> None:
        self.body += "    xchg " + a + ", " + b + "\n"

    def xlatb(self) -> None:
        self.body += "    xlatb\n"

    def pushf(self) -> None:
        self.body += "    pushf\n"

    def popf(self) -> None:
        self.body += "    popf\n"

    def pushfq(self) -> None:
        self.body += "    pushfq\n"

    def popfq(self) -> None:
        self.body += "    popfq\n"

    # ------------------------------------------------------------------
    # Arithmetic
    # ------------------------------------------------------------------

    def add(self, dest: str, src: str) -> None:
        self.body += "    add " + dest + ", " + src + "\n"

    def adc(self, dest: str, src: str) -> None:
        self.body += "    adc " + dest + ", " + src + "\n"

    def sub(self, dest: str, src: str) -> None:
        self.body += "    sub " + dest + ", " + src + "\n"

    def sbb(self, dest: str, src: str) -> None:
        self.body += "    sbb " + dest + ", " + src + "\n"

    def mul(self, src: str) -> None:
        self.body += "    mul " + src + "\n"

    def imul(self, dest: str, src: str) -> None:
        self.body += "    imul " + dest + ", " + src + "\n"

    def imul3(self, dest: str, src: str, imm: str) -> None:
        self.body += "    imul " + dest + ", " + src + ", " + imm + "\n"

    def div(self, src: str) -> None:
        self.body += "    div " + src + "\n"

    def idiv(self, src: str) -> None:
        self.body += "    idiv " + src + "\n"

    def inc(self, dest: str) -> None:
        self.body += "    inc " + dest + "\n"

    def dec(self, dest: str) -> None:
        self.body += "    dec " + dest + "\n"

    def neg(self, dest: str) -> None:
        self.body += "    neg " + dest + "\n"

    def cbw(self) -> None:
        self.body += "    cbw\n"

    def cwde(self) -> None:
        self.body += "    cwde\n"

    def cdqe(self) -> None:
        self.body += "    cdqe\n"

    def cwd(self) -> None:
        self.body += "    cwd\n"

    def cdq(self) -> None:
        self.body += "    cdq\n"

    def cqo(self) -> None:
        self.body += "    cqo\n"

    # ------------------------------------------------------------------
    # Logical
    # ------------------------------------------------------------------

    def and_(self, dest: str, src: str) -> None:
        self.body += "    and " + dest + ", " + src + "\n"

    def or_(self, dest: str, src: str) -> None:
        self.body += "    or " + dest + ", " + src + "\n"

    def xor(self, dest: str, src: str) -> None:
        self.body += "    xor " + dest + ", " + src + "\n"

    def not_(self, dest: str) -> None:
        self.body += "    not " + dest + "\n"

    def test(self, a: str, b: str) -> None:
        self.body += "    test " + a + ", " + b + "\n"

    # ------------------------------------------------------------------
    # Shifts and rotates
    # ------------------------------------------------------------------

    def shl(self, dest: str, count: str) -> None:
        self.body += "    shl " + dest + ", " + count + "\n"

    def shr(self, dest: str, count: str) -> None:
        self.body += "    shr " + dest + ", " + count + "\n"

    def sar(self, dest: str, count: str) -> None:
        self.body += "    sar " + dest + ", " + count + "\n"

    def sal(self, dest: str, count: str) -> None:
        self.body += "    sal " + dest + ", " + count + "\n"

    def rol(self, dest: str, count: str) -> None:
        self.body += "    rol " + dest + ", " + count + "\n"

    def ror(self, dest: str, count: str) -> None:
        self.body += "    ror " + dest + ", " + count + "\n"

    def rcl(self, dest: str, count: str) -> None:
        self.body += "    rcl " + dest + ", " + count + "\n"

    def rcr(self, dest: str, count: str) -> None:
        self.body += "    rcr " + dest + ", " + count + "\n"

    def shld(self, dest: str, src: str, count: str) -> None:
        self.body += "    shld " + dest + ", " + src + ", " + count + "\n"

    def shrd(self, dest: str, src: str, count: str) -> None:
        self.body += "    shrd " + dest + ", " + src + ", " + count + "\n"

    # ------------------------------------------------------------------
    # Bit manipulation
    # ------------------------------------------------------------------

    def bsf(self, dest: str, src: str) -> None:
        self.body += "    bsf " + dest + ", " + src + "\n"

    def bsr(self, dest: str, src: str) -> None:
        self.body += "    bsr " + dest + ", " + src + "\n"

    def bt(self, base: str, offset: str) -> None:
        self.body += "    bt " + base + ", " + offset + "\n"

    def bts(self, base: str, offset: str) -> None:
        self.body += "    bts " + base + ", " + offset + "\n"

    def btr(self, base: str, offset: str) -> None:
        self.body += "    btr " + base + ", " + offset + "\n"

    def btc(self, base: str, offset: str) -> None:
        self.body += "    btc " + base + ", " + offset + "\n"

    def bswap(self, reg: str) -> None:
        self.body += "    bswap " + reg + "\n"

    def popcnt(self, dest: str, src: str) -> None:
        self.body += "    popcnt " + dest + ", " + src + "\n"

    def lzcnt(self, dest: str, src: str) -> None:
        self.body += "    lzcnt " + dest + ", " + src + "\n"

    def tzcnt(self, dest: str, src: str) -> None:
        self.body += "    tzcnt " + dest + ", " + src + "\n"

    # ------------------------------------------------------------------
    # Comparisons
    # ------------------------------------------------------------------

    def cmp(self, a: str, b: str) -> None:
        self.body += "    cmp " + a + ", " + b + "\n"

    # ------------------------------------------------------------------
    # Set byte on condition
    # ------------------------------------------------------------------

    def sete(self, dest: str) -> None:
        self.body += "    sete " + dest + "\n"

    def setne(self, dest: str) -> None:
        self.body += "    setne " + dest + "\n"

    def setg(self, dest: str) -> None:
        self.body += "    setg " + dest + "\n"

    def setge(self, dest: str) -> None:
        self.body += "    setge " + dest + "\n"

    def setl(self, dest: str) -> None:
        self.body += "    setl " + dest + "\n"

    def setle(self, dest: str) -> None:
        self.body += "    setle " + dest + "\n"

    def seta(self, dest: str) -> None:
        self.body += "    seta " + dest + "\n"

    def setae(self, dest: str) -> None:
        self.body += "    setae " + dest + "\n"

    def setb(self, dest: str) -> None:
        self.body += "    setb " + dest + "\n"

    def setbe(self, dest: str) -> None:
        self.body += "    setbe " + dest + "\n"

    def sets(self, dest: str) -> None:
        self.body += "    sets " + dest + "\n"

    def setns(self, dest: str) -> None:
        self.body += "    setns " + dest + "\n"

    def seto(self, dest: str) -> None:
        self.body += "    seto " + dest + "\n"

    def setno(self, dest: str) -> None:
        self.body += "    setno " + dest + "\n"

    def setp(self, dest: str) -> None:
        self.body += "    setp " + dest + "\n"

    def setnp(self, dest: str) -> None:
        self.body += "    setnp " + dest + "\n"

    # ------------------------------------------------------------------
    # Conditional moves
    # ------------------------------------------------------------------

    def cmove(self, dest: str, src: str) -> None:
        self.body += "    cmove " + dest + ", " + src + "\n"

    def cmovne(self, dest: str, src: str) -> None:
        self.body += "    cmovne " + dest + ", " + src + "\n"

    def cmovg(self, dest: str, src: str) -> None:
        self.body += "    cmovg " + dest + ", " + src + "\n"

    def cmovge(self, dest: str, src: str) -> None:
        self.body += "    cmovge " + dest + ", " + src + "\n"

    def cmovl(self, dest: str, src: str) -> None:
        self.body += "    cmovl " + dest + ", " + src + "\n"

    def cmovle(self, dest: str, src: str) -> None:
        self.body += "    cmovle " + dest + ", " + src + "\n"

    def cmova(self, dest: str, src: str) -> None:
        self.body += "    cmova " + dest + ", " + src + "\n"

    def cmovae(self, dest: str, src: str) -> None:
        self.body += "    cmovae " + dest + ", " + src + "\n"

    def cmovb(self, dest: str, src: str) -> None:
        self.body += "    cmovb " + dest + ", " + src + "\n"

    def cmovbe(self, dest: str, src: str) -> None:
        self.body += "    cmovbe " + dest + ", " + src + "\n"

    def cmovs(self, dest: str, src: str) -> None:
        self.body += "    cmovs " + dest + ", " + src + "\n"

    def cmovns(self, dest: str, src: str) -> None:
        self.body += "    cmovns " + dest + ", " + src + "\n"

    def cmovo(self, dest: str, src: str) -> None:
        self.body += "    cmovo " + dest + ", " + src + "\n"

    def cmovno(self, dest: str, src: str) -> None:
        self.body += "    cmovno " + dest + ", " + src + "\n"

    def cmovp(self, dest: str, src: str) -> None:
        self.body += "    cmovp " + dest + ", " + src + "\n"

    def cmovnp(self, dest: str, src: str) -> None:
        self.body += "    cmovnp " + dest + ", " + src + "\n"

    # ------------------------------------------------------------------
    # Control flow
    # ------------------------------------------------------------------

    def jmp(self, target: str) -> None:
        self.body += "    jmp " + target + "\n"

    def call(self, target: str) -> None:
        self.body += "    call " + target + "\n"

    def ret(self) -> None:
        self.body += "    ret\n"

    def retn(self, n: str) -> None:
        self.body += "    ret " + n + "\n"

    def leave(self) -> None:
        self.body += "    leave\n"

    def je(self, target: str) -> None:
        self.body += "    je " + target + "\n"

    def jz(self, target: str) -> None:
        self.body += "    jz " + target + "\n"

    def jne(self, target: str) -> None:
        self.body += "    jne " + target + "\n"

    def jnz(self, target: str) -> None:
        self.body += "    jnz " + target + "\n"

    def jg(self, target: str) -> None:
        self.body += "    jg " + target + "\n"

    def jge(self, target: str) -> None:
        self.body += "    jge " + target + "\n"

    def jl(self, target: str) -> None:
        self.body += "    jl " + target + "\n"

    def jle(self, target: str) -> None:
        self.body += "    jle " + target + "\n"

    def ja(self, target: str) -> None:
        self.body += "    ja " + target + "\n"

    def jae(self, target: str) -> None:
        self.body += "    jae " + target + "\n"

    def jb(self, target: str) -> None:
        self.body += "    jb " + target + "\n"

    def jbe(self, target: str) -> None:
        self.body += "    jbe " + target + "\n"

    def js(self, target: str) -> None:
        self.body += "    js " + target + "\n"

    def jns(self, target: str) -> None:
        self.body += "    jns " + target + "\n"

    def jo(self, target: str) -> None:
        self.body += "    jo " + target + "\n"

    def jno(self, target: str) -> None:
        self.body += "    jno " + target + "\n"

    def jp(self, target: str) -> None:
        self.body += "    jp " + target + "\n"

    def jnp(self, target: str) -> None:
        self.body += "    jnp " + target + "\n"

    def jrcxz(self, target: str) -> None:
        self.body += "    jrcxz " + target + "\n"

    def jecxz(self, target: str) -> None:
        self.body += "    jecxz " + target + "\n"

    def loop(self, target: str) -> None:
        self.body += "    loop " + target + "\n"

    def loope(self, target: str) -> None:
        self.body += "    loope " + target + "\n"

    def loopne(self, target: str) -> None:
        self.body += "    loopne " + target + "\n"

    # ------------------------------------------------------------------
    # String / memory operations
    # ------------------------------------------------------------------

    def movsb(self) -> None:
        self.body += "    movsb\n"

    def movsw(self) -> None:
        self.body += "    movsw\n"

    def movsd(self) -> None:
        self.body += "    movsd\n"

    def movsq(self) -> None:
        self.body += "    movsq\n"

    def stosb(self) -> None:
        self.body += "    stosb\n"

    def stosw(self) -> None:
        self.body += "    stosw\n"

    def stosd(self) -> None:
        self.body += "    stosd\n"

    def stosq(self) -> None:
        self.body += "    stosq\n"

    def lodsb(self) -> None:
        self.body += "    lodsb\n"

    def lodsw(self) -> None:
        self.body += "    lodsw\n"

    def lodsd(self) -> None:
        self.body += "    lodsd\n"

    def lodsq(self) -> None:
        self.body += "    lodsq\n"

    def scasb(self) -> None:
        self.body += "    scasb\n"

    def scasw(self) -> None:
        self.body += "    scasw\n"

    def scasd(self) -> None:
        self.body += "    scasd\n"

    def scasq(self) -> None:
        self.body += "    scasq\n"

    def cmpsb(self) -> None:
        self.body += "    cmpsb\n"

    def cmpsw(self) -> None:
        self.body += "    cmpsw\n"

    def cmpsd(self) -> None:
        self.body += "    cmpsd\n"

    def cmpsq(self) -> None:
        self.body += "    cmpsq\n"

    def rep_movsb(self) -> None:
        self.body += "    rep movsb\n"

    def rep_movsw(self) -> None:
        self.body += "    rep movsw\n"

    def rep_movsd(self) -> None:
        self.body += "    rep movsd\n"

    def rep_movsq(self) -> None:
        self.body += "    rep movsq\n"

    def rep_stosb(self) -> None:
        self.body += "    rep stosb\n"

    def rep_stosq(self) -> None:
        self.body += "    rep stosq\n"

    def repe_cmpsb(self) -> None:
        self.body += "    repe cmpsb\n"

    def repe_cmpsq(self) -> None:
        self.body += "    repe cmpsq\n"

    def repne_scasb(self) -> None:
        self.body += "    repne scasb\n"

    def repne_scasq(self) -> None:
        self.body += "    repne scasq\n"

    # ------------------------------------------------------------------
    # Flag operations
    # ------------------------------------------------------------------

    def clc(self) -> None:
        self.body += "    clc\n"

    def stc(self) -> None:
        self.body += "    stc\n"

    def cmc(self) -> None:
        self.body += "    cmc\n"

    def cld(self) -> None:
        self.body += "    cld\n"

    def std(self) -> None:
        self.body += "    std\n"

    def lahf(self) -> None:
        self.body += "    lahf\n"

    def sahf(self) -> None:
        self.body += "    sahf\n"

    # ------------------------------------------------------------------
    # Atomics and synchronization
    # ------------------------------------------------------------------

    def xadd(self, dest: str, src: str) -> None:
        self.body += "    xadd " + dest + ", " + src + "\n"

    def cmpxchg(self, dest: str, src: str) -> None:
        self.body += "    cmpxchg " + dest + ", " + src + "\n"

    def cmpxchg8b(self, mem: str) -> None:
        self.body += "    cmpxchg8b " + mem + "\n"

    def cmpxchg16b(self, mem: str) -> None:
        self.body += "    cmpxchg16b " + mem + "\n"

    def lock_add(self, dest: str, src: str) -> None:
        self.body += "    lock add " + dest + ", " + src + "\n"

    def lock_sub(self, dest: str, src: str) -> None:
        self.body += "    lock sub " + dest + ", " + src + "\n"

    def lock_and(self, dest: str, src: str) -> None:
        self.body += "    lock and " + dest + ", " + src + "\n"

    def lock_or(self, dest: str, src: str) -> None:
        self.body += "    lock or " + dest + ", " + src + "\n"

    def lock_xor(self, dest: str, src: str) -> None:
        self.body += "    lock xor " + dest + ", " + src + "\n"

    def lock_xadd(self, dest: str, src: str) -> None:
        self.body += "    lock xadd " + dest + ", " + src + "\n"

    def lock_cmpxchg(self, dest: str, src: str) -> None:
        self.body += "    lock cmpxchg " + dest + ", " + src + "\n"

    def lock_inc(self, dest: str) -> None:
        self.body += "    lock inc " + dest + "\n"

    def lock_dec(self, dest: str) -> None:
        self.body += "    lock dec " + dest + "\n"

    def mfence(self) -> None:
        self.body += "    mfence\n"

    def lfence(self) -> None:
        self.body += "    lfence\n"

    def sfence(self) -> None:
        self.body += "    sfence\n"

    # ------------------------------------------------------------------
    # System / privileged
    # ------------------------------------------------------------------

    def syscall(self) -> None:
        self.body += "    syscall\n"

    def sysret(self) -> None:
        self.body += "    sysret\n"

    def nop(self) -> None:
        self.body += "    nop\n"

    def hlt(self) -> None:
        self.body += "    hlt\n"

    def int_(self, n: str) -> None:
        self.body += "    int " + n + "\n"

    def int3(self) -> None:
        self.body += "    int3\n"

    def iretq(self) -> None:
        self.body += "    iretq\n"

    def in_(self, dest: str, src: str) -> None:
        self.body += "    in " + dest + ", " + src + "\n"

    def out(self, dest: str, src: str) -> None:
        self.body += "    out " + dest + ", " + src + "\n"

    def cpuid(self) -> None:
        self.body += "    cpuid\n"

    def rdtsc(self) -> None:
        self.body += "    rdtsc\n"

    def rdtscp(self) -> None:
        self.body += "    rdtscp\n"

    def pause(self) -> None:
        self.body += "    pause\n"

    # ------------------------------------------------------------------
    # SSE scalar
    # ------------------------------------------------------------------

    def movss(self, dest: str, src: str) -> None:
        self.body += "    movss " + dest + ", " + src + "\n"

    def movsd_f(self, dest: str, src: str) -> None:
        self.body += "    movsd " + dest + ", " + src + "\n"

    def addss(self, dest: str, src: str) -> None:
        self.body += "    addss " + dest + ", " + src + "\n"

    def addsd(self, dest: str, src: str) -> None:
        self.body += "    addsd " + dest + ", " + src + "\n"

    def subss(self, dest: str, src: str) -> None:
        self.body += "    subss " + dest + ", " + src + "\n"

    def subsd(self, dest: str, src: str) -> None:
        self.body += "    subsd " + dest + ", " + src + "\n"

    def mulss(self, dest: str, src: str) -> None:
        self.body += "    mulss " + dest + ", " + src + "\n"

    def mulsd(self, dest: str, src: str) -> None:
        self.body += "    mulsd " + dest + ", " + src + "\n"

    def divss(self, dest: str, src: str) -> None:
        self.body += "    divss " + dest + ", " + src + "\n"

    def divsd(self, dest: str, src: str) -> None:
        self.body += "    divsd " + dest + ", " + src + "\n"

    def sqrtss(self, dest: str, src: str) -> None:
        self.body += "    sqrtss " + dest + ", " + src + "\n"

    def sqrtsd(self, dest: str, src: str) -> None:
        self.body += "    sqrtsd " + dest + ", " + src + "\n"

    def minss(self, dest: str, src: str) -> None:
        self.body += "    minss " + dest + ", " + src + "\n"

    def minsd(self, dest: str, src: str) -> None:
        self.body += "    minsd " + dest + ", " + src + "\n"

    def maxss(self, dest: str, src: str) -> None:
        self.body += "    maxss " + dest + ", " + src + "\n"

    def maxsd(self, dest: str, src: str) -> None:
        self.body += "    maxsd " + dest + ", " + src + "\n"

    def ucomiss(self, a: str, b: str) -> None:
        self.body += "    ucomiss " + a + ", " + b + "\n"

    def ucomisd(self, a: str, b: str) -> None:
        self.body += "    ucomisd " + a + ", " + b + "\n"

    def cvtsi2sd(self, dest: str, src: str) -> None:
        self.body += "    cvtsi2sd " + dest + ", " + src + "\n"

    def cvtsd2si(self, dest: str, src: str) -> None:
        self.body += "    cvtsd2si " + dest + ", " + src + "\n"

    def cvtsi2ss(self, dest: str, src: str) -> None:
        self.body += "    cvtsi2ss " + dest + ", " + src + "\n"

    def cvtss2si(self, dest: str, src: str) -> None:
        self.body += "    cvtss2si " + dest + ", " + src + "\n"

    def cvttsd2si(self, dest: str, src: str) -> None:
        self.body += "    cvttsd2si " + dest + ", " + src + "\n"

    def cvttss2si(self, dest: str, src: str) -> None:
        self.body += "    cvttss2si " + dest + ", " + src + "\n"

    def cvtss2sd(self, dest: str, src: str) -> None:
        self.body += "    cvtss2sd " + dest + ", " + src + "\n"

    def cvtsd2ss(self, dest: str, src: str) -> None:
        self.body += "    cvtsd2ss " + dest + ", " + src + "\n"

    # ------------------------------------------------------------------
    # SSE packed
    # ------------------------------------------------------------------

    def movaps(self, dest: str, src: str) -> None:
        self.body += "    movaps " + dest + ", " + src + "\n"

    def movups(self, dest: str, src: str) -> None:
        self.body += "    movups " + dest + ", " + src + "\n"

    def movapd(self, dest: str, src: str) -> None:
        self.body += "    movapd " + dest + ", " + src + "\n"

    def movupd(self, dest: str, src: str) -> None:
        self.body += "    movupd " + dest + ", " + src + "\n"

    def movdqu(self, dest: str, src: str) -> None:
        self.body += "    movdqu " + dest + ", " + src + "\n"

    def movdqa(self, dest: str, src: str) -> None:
        self.body += "    movdqa " + dest + ", " + src + "\n"

    def movq(self, dest: str, src: str) -> None:
        self.body += "    movq " + dest + ", " + src + "\n"

    def movd(self, dest: str, src: str) -> None:
        self.body += "    movd " + dest + ", " + src + "\n"

    def xorps(self, dest: str, src: str) -> None:
        self.body += "    xorps " + dest + ", " + src + "\n"

    def xorpd(self, dest: str, src: str) -> None:
        self.body += "    xorpd " + dest + ", " + src + "\n"

    def andps(self, dest: str, src: str) -> None:
        self.body += "    andps " + dest + ", " + src + "\n"

    def andpd(self, dest: str, src: str) -> None:
        self.body += "    andpd " + dest + ", " + src + "\n"

    def orps(self, dest: str, src: str) -> None:
        self.body += "    orps " + dest + ", " + src + "\n"

    def orpd(self, dest: str, src: str) -> None:
        self.body += "    orpd " + dest + ", " + src + "\n"

    def addps(self, dest: str, src: str) -> None:
        self.body += "    addps " + dest + ", " + src + "\n"

    def addpd(self, dest: str, src: str) -> None:
        self.body += "    addpd " + dest + ", " + src + "\n"

    def subps(self, dest: str, src: str) -> None:
        self.body += "    subps " + dest + ", " + src + "\n"

    def subpd(self, dest: str, src: str) -> None:
        self.body += "    subpd " + dest + ", " + src + "\n"

    def mulps(self, dest: str, src: str) -> None:
        self.body += "    mulps " + dest + ", " + src + "\n"

    def mulpd(self, dest: str, src: str) -> None:
        self.body += "    mulpd " + dest + ", " + src + "\n"

    def pxor(self, dest: str, src: str) -> None:
        self.body += "    pxor " + dest + ", " + src + "\n"

    def pand(self, dest: str, src: str) -> None:
        self.body += "    pand " + dest + ", " + src + "\n"

    def por(self, dest: str, src: str) -> None:
        self.body += "    por " + dest + ", " + src + "\n"

    def pcmpeqb(self, dest: str, src: str) -> None:
        self.body += "    pcmpeqb " + dest + ", " + src + "\n"

    def pcmpeqd(self, dest: str, src: str) -> None:
        self.body += "    pcmpeqd " + dest + ", " + src + "\n"

    def pcmpgtb(self, dest: str, src: str) -> None:
        self.body += "    pcmpgtb " + dest + ", " + src + "\n"

    def pmovmskb(self, dest: str, src: str) -> None:
        self.body += "    pmovmskb " + dest + ", " + src + "\n"

    def movmskps(self, dest: str, src: str) -> None:
        self.body += "    movmskps " + dest + ", " + src + "\n"

    def pshufd(self, dest: str, src: str, imm: str) -> None:
        self.body += "    pshufd " + dest + ", " + src + ", " + imm + "\n"

    def pshufb(self, dest: str, src: str) -> None:
        self.body += "    pshufb " + dest + ", " + src + "\n"

    def punpcklbw(self, dest: str, src: str) -> None:
        self.body += "    punpcklbw " + dest + ", " + src + "\n"

    def paddq(self, dest: str, src: str) -> None:
        self.body += "    paddq " + dest + ", " + src + "\n"

    def paddb(self, dest: str, src: str) -> None:
        self.body += "    paddb " + dest + ", " + src + "\n"

    def paddw(self, dest: str, src: str) -> None:
        self.body += "    paddw " + dest + ", " + src + "\n"

    def paddd(self, dest: str, src: str) -> None:
        self.body += "    paddd " + dest + ", " + src + "\n"

    def psubb(self, dest: str, src: str) -> None:
        self.body += "    psubb " + dest + ", " + src + "\n"

    def psubw(self, dest: str, src: str) -> None:
        self.body += "    psubw " + dest + ", " + src + "\n"

    def psubd(self, dest: str, src: str) -> None:
        self.body += "    psubd " + dest + ", " + src + "\n"

    def psubq(self, dest: str, src: str) -> None:
        self.body += "    psubq " + dest + ", " + src + "\n"

    def psrlq(self, dest: str, count: str) -> None:
        self.body += "    psrlq " + dest + ", " + count + "\n"

    def psllq(self, dest: str, count: str) -> None:
        self.body += "    psllq " + dest + ", " + count + "\n"

    def psrldq(self, dest: str, count: str) -> None:
        self.body += "    psrldq " + dest + ", " + count + "\n"

    def pslldq(self, dest: str, count: str) -> None:
        self.body += "    pslldq " + dest + ", " + count + "\n"

    def shufps(self, dest: str, src: str, imm: str) -> None:
        self.body += "    shufps " + dest + ", " + src + ", " + imm + "\n"

    def shufpd(self, dest: str, src: str, imm: str) -> None:
        self.body += "    shufpd " + dest + ", " + src + ", " + imm + "\n"

    def blendps(self, dest: str, src: str, imm: str) -> None:
        self.body += "    blendps " + dest + ", " + src + ", " + imm + "\n"

    def blendpd(self, dest: str, src: str, imm: str) -> None:
        self.body += "    blendpd " + dest + ", " + src + ", " + imm + "\n"


def asm_func(
    fn: Callable | None = None, *, symbol: str | None = None
) -> AssemblyFunc | _AsmFuncDecorator:
    """Mark a function as having a raw-NASM body (see module docstring).

    Usable bare (`@asm_func`) or parameterised
    (`@asm_func(symbol="my_add")`) to override the emitted symbol name.
    """
    if fn is None:
        # Called with arguments: @asm_func(symbol=...). Return the real
        # decorator.
        return _AsmFuncDecorator(symbol)
    return AssemblyFunc(fn, symbol=symbol)



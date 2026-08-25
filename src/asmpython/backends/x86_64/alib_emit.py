"""Lowering `x86_64.alib` -- the intrinsics, as instructions.

WHAT MAKES THIS DIFFERENT FROM A CALL. Every entry here is realised INLINE.
`outb` is not a function this emits a call to; it is two moves and an `out`,
spliced where the call would have been. That is the whole bargain
`backend/alib.py` describes: what travels from an architecture to a backend
is a DESCRIPTION, and the backend decides how to spell it.

THE ARGUMENTS ARRIVE WHERE THE ABI PUT THEM. The CALL path in `emit.py`
places arguments before it looks at the symbol, so by the time a sequence
here runs, argument one is in `%rdi`, two in `%rsi`, and so on. Each
sequence's job is to move them wherever the instruction INSISTS they live --
`out` insists on `%dx` and `%al`, `wrmsr` on `%ecx` and `%edx:%eax`, `lidt`
on a memory operand -- and to leave any result in `%rax`, which is where the
caller already looks.

THOSE INSISTENCES ARE THE ENTIRE REASON alib EXISTS. There is no way to
write "put this value in %dx and this one in %al" in a language; a compiler
either knows the instruction or it cannot emit it.

WIDTHS ARE NOT DECORATION. `inb` writes only `%al` and leaves the rest of
`%rax` holding whatever the caller had, so the register is zeroed first --
without that, reading a UART status byte returns a plausible number with
someone else's high bits attached, and nothing faults. `inl` needs no such
zeroing because a 32-bit write clears the upper half; that asymmetry is the
architecture's, not a style choice.
"""
from __future__ import annotations

#: `<intrinsic name>` -> the AT&T instruction lines that realise it.
#:
#: Arguments: %rdi, %rsi, %rdx, %rcx (System V). Result: %rax.
#:
#: A VOID INTRINSIC STILL LEAVES A VALUE. `void` has no spelling in the
#: marshalling path this rides on, so it is carried as `i64` and every such
#: sequence ends by zeroing `%eax`. Without that, `outb(...)` would evaluate
#: to whatever the `out` instruction happened to leave in the register --
#: harmless if ignored and baffling if printed.
#:
#: A name absent from this table is declared but not emitted, and `emit.py`
#: refuses it by name rather than silently emitting a call to a symbol that
#: does not exist -- "a declared intrinsic a backend silently ignored would
#: be the worst of the three states".
LOWERINGS: dict[str, tuple[str, ...]] = {

    # ---- ports -----------------------------------------------------------
    # The second address space, and the only two instructions that can see
    # it. The port number must be in %dx (or an 8-bit immediate, which a
    # computed argument never is).
    "outb": ("movw %di, %dx", "movb %sil, %al", "outb %al, %dx", "xorl %eax, %eax"),
    "outw": ("movw %di, %dx", "movw %si, %ax", "outw %ax, %dx", "xorl %eax, %eax"),
    "outl": ("movw %di, %dx", "movl %esi, %eax", "outl %eax, %dx", "xorl %eax, %eax"),
    "inb": ("movw %di, %dx", "xorl %eax, %eax", "inb %dx, %al"),
    "inw": ("movw %di, %dx", "xorl %eax, %eax", "inw %dx, %ax"),
    "inl": ("movw %di, %dx", "inl %dx, %eax"),

    # ---- memory-mapped I/O ------------------------------------------------
    # A load and a store, and the promise that they happen exactly once.
    # Nothing here needs a barrier: x86 does not reorder a load past a load
    # or a store past a store, and the compiler cannot move these because it
    # never sees them -- they are emitted after every optimisation has run.
    "mmio8_read": ("movzbl (%rdi), %eax",),
    "mmio16_read": ("movzwl (%rdi), %eax",),
    "mmio32_read": ("movl (%rdi), %eax",),
    "mmio64_read": ("movq (%rdi), %rax",),
    # THE STRING INSTRUCTIONS. `rep stosl` writes %eax to ES:[%rdi] %rcx
    # times, advancing %rdi; `rep movsl` copies DS:[%rsi] to ES:[%rdi]. Both
    # go FORWARD only while the direction flag is clear -- the ABI says it is
    # on entry, but `cld` costs one byte and removes the assumption.
    "mmio_fill32": ("cld", "movq %rdx, %rcx", "movl %esi, %eax",
                    "rep stosl", "xorl %eax, %eax"),
    "mmio_copy32": ("cld", "movq %rdx, %rcx",
                    "rep movsl", "xorl %eax, %eax"),
    "mmio8_write": ("movb %sil, (%rdi)", "xorl %eax, %eax"),
    "mmio16_write": ("movw %si, (%rdi)", "xorl %eax, %eax"),
    "mmio32_write": ("movl %esi, (%rdi)", "xorl %eax, %eax"),
    "mmio64_write": ("movq %rsi, (%rdi)", "xorl %eax, %eax"),

    # ---- named instructions ------------------------------------------------
    # THE TARGET MUST LEAVE %rdi BEFORE %rdi BECOMES AN ARGUMENT. Shifting
    # the other way round -- arguments first -- overwrites the address with
    # the first argument and calls whatever that happens to be.
    "native_call": ("movq %rdi, %rax",
                    "movq %rsi, %rdi",
                    "movq %rdx, %rsi",
                    "movq %rcx, %rdx",
                    "movq %r8, %rcx",
                    "movq %r9, %r8",
                    "call *%rax"),
    "pause": ("pause", "xorl %eax, %eax"),
    "hlt": ("hlt", "xorl %eax, %eax"),
    "popcnt64": ("popcntq %rdi, %rax",),
    "bswap64": ("movq %rdi, %rax", "bswapq %rax"),
    # rdtsc splits its answer across two registers, as every 64-bit read on
    # this architecture does. Rejoining is two instructions and is the only
    # reason these are not one line each.
    "rdtsc": ("rdtsc", "shlq $32, %rdx", "orq %rdx, %rax"),
    "rdtscp": ("rdtscp", "shlq $32, %rdx", "orq %rdx, %rax"),
    "rdrand64": ("rdrand %rax",),

    # ---- barriers -----------------------------------------------------------
    "mfence": ("mfence", "xorl %eax, %eax"),
    "sfence": ("sfence", "xorl %eax, %eax"),
    "lfence": ("lfence", "xorl %eax, %eax"),
    "clflush": ("clflush (%rdi)", "xorl %eax, %eax"),

    # ---- system registers ---------------------------------------------------
    # `rdmsr` and `wrmsr` name their register in %ecx and pass the value as
    # %edx:%eax. The join and the split below are what make them look like
    # ordinary 64-bit functions to the caller.
    "rdmsr": ("movl %edi, %ecx", "rdmsr", "shlq $32, %rdx", "orq %rdx, %rax"),
    "wrmsr": ("movl %edi, %ecx", "movq %rsi, %rax", "movq %rsi, %rdx",
              "shrq $32, %rdx", "wrmsr", "xorl %eax, %eax"),
    # A control register move is ALWAYS 64 bits in long mode, so it takes no
    # REX.W and the assembler writes it without one.
    "read_cr0": ("movq %cr0, %rax",),
    # cr2 holds the address that faulted, and only means anything inside a
    # page-fault handler -- reading it anywhere else answers whatever the
    # last fault was.
    "read_cr2": ("movq %cr2, %rax",),
    "read_cr4": ("movq %cr4, %rax",),
    "write_cr0": ("movq %rdi, %cr0", "xorl %eax, %eax"),
    "write_cr4": ("movq %rdi, %cr4", "xorl %eax, %eax"),
    "invlpg": ("invlpg (%rdi)", "xorl %eax, %eax"),
    "read_cr3": ("movq %cr3, %rax",),
    "write_cr3": ("movq %rdi, %cr3", "xorl %eax, %eax"),

    # ---- interrupts ----------------------------------------------------------
    "cli": ("cli", "xorl %eax, %eax"),
    "sti": ("sti", "xorl %eax, %eax"),
    # `lidt` takes the ADDRESS of a ten-byte limit-and-base pair, not the two
    # values. The caller builds that structure and passes where it put it,
    # which is why this is a memory operand and not two arguments.
    "lidt": ("lidt (%rdi)", "xorl %eax, %eax"),
    "lgdt": ("lgdt (%rdi)", "xorl %eax, %eax"),
    # `ltr` takes a SELECTOR, a 16-bit value, not an address.
    "ltr": ("ltr %di", "xorl %eax, %eax"),
}


def is_lowered(symbol: str) -> bool:
    return symbol in LOWERINGS


def lines(symbol: str) -> tuple[str, ...]:
    return LOWERINGS[symbol]

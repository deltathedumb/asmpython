"""Demonstrates asmpython.assembly: inline NASM functions + included packages.

Compile and run (Windows shown; on Linux drop the .exe and use asmpython.sh):

    python -m asmpython examples/assembly_demo.py -o build/asmdemo.exe
    build/asmdemo.exe

`@assembly_func` gives a function a raw-NASM body that asmpython emits verbatim;
the Python signature is the contract (symbol name + arg/return types). Arguments
arrive in the target ABI's integer registers — rdi, rsi, rdx, ... on System V;
rcx, rdx, r8, ... on Win64 — and the body returns its result in rax.
"""

from asmpython.assembly import assembly_func


@assembly_func
def triple(x: int) -> int:
    """
    ; first integer arg: rdi (SysV) / rcx (Win64). This body targets Win64.
    mov rax, rcx
    imul rax, 3
    ret
    """


@assembly_func
def maxi(a: int, b: int) -> int:
    """
    ; max(a, b) — second arg in rdx (Win64)
    mov rax, rcx
    cmp rax, rdx
    jge .done
    mov rax, rdx
.done:
    ret
    """


def run():
    print(triple(14))      # 42
    print(maxi(3, 9))      # 9
    print(maxi(20, 5))     # 20


run()

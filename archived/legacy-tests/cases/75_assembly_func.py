# expect:
# 42
# 7
# 100

# Exercises @assembly_func: a function whose body is raw NASM, called like any
# other asmpython function. To stay target-agnostic (the integer argument
# registers differ between Win64 and System V), these bodies return constants
# rather than reading their arguments — that still proves the call routes into
# the inline assembly and the result comes back in rax.

from asmpython.assembly import assembly_func


@assembly_func
def answer() -> int:
    """
    mov rax, 42
    ret
    """


@assembly_func
def lucky() -> int:
    """
    mov rax, 7
    ret
    """


def run():
    print(answer())
    print(lucky())
    print(answer() + lucky() + 51)


run()

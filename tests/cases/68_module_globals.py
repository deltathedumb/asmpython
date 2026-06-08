# expect:
# 7
# 100
# 42
# greeting=hi there
# count=3
# 30
BASE = 5
GREETING = "hi there"
LIMIT = 100


def add_base(n: int) -> int:
    # BASE is a module global here, not a parameter or local — it lives in
    # .bss and is read through its symbol, not off this frame.
    return n + BASE


def get_limit() -> int:
    return LIMIT


def greet() -> str:
    return GREETING


# A mutable module global, written at top level then read in a function.
counter = 0


def bump() -> int:
    return counter


print(add_base(2))             # 7
print(get_limit())             # 100
print(add_base(37))            # 42
print("greeting=" + greet())   # greeting=hi there
counter = 3
print("count=" + str(bump()))  # count=3
print(get_limit() - 70 + BASE - 5)  # 30

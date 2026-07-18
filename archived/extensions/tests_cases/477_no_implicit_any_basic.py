# ext: no_implicit_any
# expect:
# 6

def add(a: int, b: int) -> int:
    return a + b

def use_it(a, b) -> int:
    return add(a, b) + 1

print(use_it(2, 3))

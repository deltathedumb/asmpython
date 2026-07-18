# ext: no_shadowing
# expect:
# 12

def add(a: int, b: int) -> int:
    total = a + b
    return total

print(add(5, 7))

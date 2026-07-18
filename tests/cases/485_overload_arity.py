# ext: overload
# expect:
# 5
# 12

@overload
def combine(a: int) -> int:
    return a

@overload
def combine(a: int, b: int) -> int:
    return a + b

print(combine(5))
print(combine(5, 7))

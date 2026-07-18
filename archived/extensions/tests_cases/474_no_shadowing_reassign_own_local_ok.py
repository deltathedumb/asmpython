# ext: no_shadowing
# expect:
# 11

def bump(x: int) -> int:
    x = x + 1
    x = x + 1
    return x

print(bump(9))

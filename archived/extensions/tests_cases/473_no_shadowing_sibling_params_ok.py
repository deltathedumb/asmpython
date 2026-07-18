# ext: no_shadowing
# expect:
# 5
# 25

def double(x: int) -> int:
    return x * 2

def square(x: int) -> int:
    return x * x

print(double(2) + 1)
print(square(5))

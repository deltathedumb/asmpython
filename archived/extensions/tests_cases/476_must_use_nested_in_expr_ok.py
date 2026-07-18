# ext: must_use
# expect:
# 16

@must_use
def square(n: int) -> int:
    return n * n

print(square(4))

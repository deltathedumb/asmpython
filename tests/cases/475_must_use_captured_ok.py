# ext: must_use
# expect:
# 9

@must_use
def square(n: int) -> int:
    return n * n

x = square(3)
print(x)

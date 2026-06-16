# probe16: nested functions, closures, default args, *args
def add(a: int, b: int = 10) -> int:
    return a + b

print(add(5))
print(add(5, 3))

# nested function
def outer(x: int) -> int:
    def inner(y: int) -> int:
        return x + y
    return inner(3)

print(outer(7))

# multiple return values (tuple unpacking)
def minmax(xs: list) -> tuple:
    return min(xs), max(xs)

lo, hi = minmax([3, 1, 4, 1, 5])
print(lo)
print(hi)

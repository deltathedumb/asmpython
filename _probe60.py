# probe60: more edge cases
# Multiple return values
def minmax(xs: list[int]) -> tuple[int, int]:
    mn = xs[0]
    mx = xs[0]
    for x in xs[1:]:
        if x < mn:
            mn = x
        if x > mx:
            mx = x
    return mn, mx

lo, hi = minmax([3, 1, 4, 1, 5, 9, 2, 6])
print(lo, hi)  # 1 9

# Nested tuples
def swap(a: int, b: int) -> tuple[int, int]:
    return b, a

x, y = swap(3, 7)
print(x, y)  # 7 3

# Chained function calls
def double(n: int) -> int:
    return n * 2

def add1(n: int) -> int:
    return n + 1

print(double(add1(double(3))))  # double(add1(6)) = double(7) = 14

# List of function results
results = [double(i) for i in range(5)]
print(results)  # [0, 2, 4, 6, 8]

# String methods chained
s = "  Hello, World!  "
print(s.strip().lower().replace("hello", "hi"))  # hi, world!

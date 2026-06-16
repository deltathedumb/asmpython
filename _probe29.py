# probe29: f-string complex, star unpacking, multiple returns from functions
# f-string with method calls
name = "alice"
n = 42
print(f"Hello {name.upper()}, you have {n} items")

# f-string with arithmetic
x = 3
y = 4
print(f"{x}^2 + {y}^2 = {x*x + y*y}")

# star unpack in function call (if supported)
def add3(a: int, b: int, c: int) -> int:
    return a + b + c

args = [1, 2, 3]
# print(add3(*args))   # star-unpack likely unsupported

# chained method calls
s = "  hello world  "
print(s.strip().upper().replace("HELLO", "HI"))

# conditional expression in list comp
xs = [x if x > 0 else -x for x in [-3, -1, 0, 2, 5]]
print(xs[0])  # 3
print(xs[3])  # 2

# probe95: complex f-strings with expressions, nested calls
name = "world"
n = 42
lst = [1, 2, 3]

# Basic expressions in f-strings
print(f"hello {name}!")             # hello world!
print(f"n = {n}")                   # n = 42
print(f"n*2 = {n * 2}")            # n*2 = 84
print(f"len = {len(lst)}")         # len = 3
print(f"sum = {sum(lst)}")         # sum = 6
print(f"first = {lst[0]}")         # first = 1

# Conditional in f-string
print(f"{'yes' if n > 0 else 'no'}")   # yes

# Nested f-string
inner = f"inner {n}"
print(f"outer: {inner}")   # outer: inner 42

# Method call in f-string
print(f"{name.upper()}")   # WORLD
print(f"{str(n).zfill(5)}")   # 00042

# Multiple expressions
a, b = 3, 4
print(f"{a} + {b} = {a + b}")   # 3 + 4 = 7
print(f"hyp = {a*a + b*b}")     # hyp = 25

# f-string in loop
for i in range(3):
    print(f"item {i}: {i * i}")

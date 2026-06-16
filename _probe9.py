# probe: more patterns

# 1. Closures / nested functions
def make_adder(n: int):
    def add(x: int) -> int:
        return x + n
    return add

# (closures with free variables may not work — probe)

# 2. Multiple assignment
a = b = c = 0
a += 1
b += 2
c += 3
print(a, b, c)

# 3. Tuple swap
x, y = 1, 2
x, y = y, x
print(x, y)

# 4. Extended unpacking (*)
first, *rest = [1, 2, 3, 4, 5]
print(first)
print(len(rest))

# 5. f-strings
name = "world"
n = 42
s = f"hello {name}, number {n}"
print(s)

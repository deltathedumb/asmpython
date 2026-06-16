# probe59: string formatting
# %-style format
name = "world"
age = 30
print("Hello, %s! Age: %d" % (name, age))  # Hello, world! Age: 30

# f-strings with various specs
pi = 3.14159
print(f"{pi:.2f}")   # 3.14
print(f"{pi:.4f}")   # 3.1416

x = 42
print(f"{x:d}")    # 42
print(f"{x:5d}")   # "   42" (width 5, right-aligned)
print(f"{x:05d}")  # 00042

# f-string with expressions
a = 3
b = 4
print(f"{a} * {b} = {a * b}")  # 3 * 4 = 12

# String format with names
s = "Hello, {name}!".format(name="Alice")
print(s)   # Hello, Alice!

# Repr conversion
print(f"{'hello'!r}")  # 'hello'

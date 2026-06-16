# probe126: float operations and formatting
x = 3.14159
y = 2.71828

print(x + y)         # 5.85987
print(x * y)         # 8.53972...
print(x / y)         # 1.1557...
print(x - y)         # 0.42331

# Float comparisons
print(x > y)         # True
print(x == 3.14159)  # True
print(x != y)        # True

# Float formatting
print(f"{x:.2f}")    # 3.14
print(f"{x:.4f}")    # 3.1416
print(f"{y:.3f}")    # 2.718

# Float to int
print(int(3.9))      # 3
print(int(-3.9))     # -3

# Int to float
f = float(42)
print(f)             # 42.0

# Float arithmetic
total = 0.0
for i in range(1, 6):
    total += float(i)
print(f"{total:.1f}")   # 15.0

# round
print(round(3.14159, 2))   # 3.14
print(round(2.71828, 3))   # 2.718
print(round(2.5))          # 2 or 3 depending on impl
print(round(3.5))          # 4 or 3 depending on impl

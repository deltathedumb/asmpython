# f-string with method calls
name = "alice"
n = 42
print(f"Hello {name.upper()}, you have {n} items")

# f-string with arithmetic
a = 3
b = 4
print(f"{a}^2 + {b}^2 = {a*a + b*b}")

# chained method calls
s = "  hello world  "
print(s.strip().upper().replace("HELLO", "HI"))

# conditional expression in list comp (using different var name)
ns = [v if v > 0 else -v for v in [-3, -1, 0, 2, 5]]
print(ns[0])  # 3
print(ns[3])  # 2

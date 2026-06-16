# probe50: more patterns
# String multiplication
s = "ab" * 3
print(s)   # ababab

# List concatenation
a = [1, 2] + [3, 4]
print(a)   # [1, 2, 3, 4]

# Chained comparisons
x = 5
print(1 < x < 10)   # True
print(1 < x < 4)    # False
print(x == 5 == 5)  # True

# Multiple assignment
a = b = c = 0
print(a, b, c)  # 0 0 0

# Augmented assignments
n = 10
n += 5
print(n)   # 15
n -= 3
print(n)   # 12
n *= 2
print(n)   # 24
n //= 3
print(n)   # 8
n **= 2
print(n)   # 64
n %= 10
print(n)   # 4

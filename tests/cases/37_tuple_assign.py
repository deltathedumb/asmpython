# expect:
# 1
# 2
# 2
# 1
# 10
# 20
# 30
# 8
# 13
a, b = 1, 2
print(a)
print(b)

# Classic swap: requires parallel-evaluation semantics.
a, b = b, a
print(a)
print(b)

x, y, z = 10, 20, 30
print(x)
print(y)
print(z)

# Tuple assign inside a loop (Fibonacci two-state).
prev = 0
curr = 1
i = 0
while i < 6:
    prev, curr = curr, prev + curr
    i = i + 1
print(prev)
print(curr)

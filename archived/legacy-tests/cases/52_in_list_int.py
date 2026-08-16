# expect:
# 1
# 0
# 1
# 0
# 1
# 1

xs = [10, 20, 30, 40, 50]
print(int(20 in xs))
print(int(99 in xs))
print(int(50 in xs))
print(int(50 not in xs))
print(int(0 not in xs))

# In a literal directly.
print(int(3 in [1, 2, 3]))

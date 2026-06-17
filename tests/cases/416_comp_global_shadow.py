# expect:
# 2
# 4
# 6
# 7

# Comprehension loop variable shadows a module-level global
x = 7
xs = [x * 2 for x in [1, 2, 3]]
print(xs[0])   # 2
print(xs[1])   # 4
print(xs[2])   # 6
print(x)       # 7  (global unchanged)

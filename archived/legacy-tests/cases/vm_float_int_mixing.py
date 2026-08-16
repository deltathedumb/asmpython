# probes: int and float mix without losing kind
# expect:
# 3.5
# 2.5
# 1.0
# 2
x = 1
y = 2.5
print(x + y)
print(x * y)
print(float(x))
print(int(y))

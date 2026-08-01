# tier: spec
# ref: library/functions.html#int
# expect:
# 2.7 2
# -2.7 -2
# 2.0 2
# -0.5 0
# 3 -3
# int
# 2.67
for v in (2.7, -2.7, 2.0, -0.5):
    print(v, int(v))
print(round(2.7), round(-2.7))
print(type(round(2.7)).__name__)
print(round(2.675, 2))

# tier: spec
# ref: library/functions.html#round
# expect:
# 1200
# 1200
# 1400
# 2.35
# 2 4 -2
# int float
print(round(1234, -2))
print(round(1250, -2))
print(round(1350, -2))
print(round(2.345, 2))
print(round(2.5), round(3.5), round(-2.5))
print(type(round(2.5)).__name__, type(round(2.5, 1)).__name__)

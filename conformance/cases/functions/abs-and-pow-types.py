# tier: spec
# ref: library/functions.html#abs
# expect:
# 3 3.5 3
# int float
# 8 8
# 8.0 float
# 5.0
print(abs(-3), abs(-3.5), abs(3))
print(type(abs(-3)).__name__, type(abs(-3.5)).__name__)
print(pow(2, 3), 2 ** 3)
print(pow(2.0, 3), type(pow(2.0, 3)).__name__)
print(abs(complex(3, 4)))

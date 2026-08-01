# tier: spec
# ref: library/functions.html#pow
# expect:
# 1024
# 24
# 1
# int
print(pow(2, 10))
print(pow(2, 10, 1000))
print(pow(3, 0))
print(type(pow(2, 10)).__name__)

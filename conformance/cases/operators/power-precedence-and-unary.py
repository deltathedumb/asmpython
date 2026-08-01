# tier: spec
# ref: reference/expressions.html#the-power-operator
# expect:
# -4
# 4
# 512
# 0.5
# float
print(-2 ** 2)
print((-2) ** 2)
print(2 ** 3 ** 2)
print(2 ** -1)
print(type(2 ** -1).__name__)

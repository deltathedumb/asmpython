# tier: spec
# ref: library/stdtypes.html#typesnumeric
# expect:
# inf -inf
# nan
# False
# True
# 0.0
inf = float("inf")
nan = float("nan")
print(inf, -inf)
print(nan)
print(nan == nan)
print(inf > 10 ** 100)
print(1 / inf)

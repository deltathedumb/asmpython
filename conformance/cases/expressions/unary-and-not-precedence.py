# tier: spec
# ref: reference/expressions.html#unary-arithmetic-and-bitwise-operations
# expect:
# True
# -1 1
# 0 2
# False
# no-unary-on-str
# True
print(not 1 == 2)
print(-1 ** 2, (-1) ** 2)
print(~-1, -~1)
print(not not [])
print(+"" if False else "no-unary-on-str")
print(-0.0 == 0.0)

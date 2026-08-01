# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# abc
# 42
# 3.5
# True
# None
# [1]
# {'k': 1}
# (1, 2)
def ident(v):
    return v

for x in ['abc', 42, 3.5, True, None, [1], {'k': 1}, (1, 2)]:
    print(ident(x))

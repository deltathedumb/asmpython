# tier: spec
# ref: reference/datamodel.html#object.__eq__
# expect:
# True False
# True
# True False
# 2
class NoEq:
    pass

a, b = NoEq(), NoEq()
print(a == a, a == b)
print(a != b)
print(a in [a], a in [b])
print(len({a, b}))

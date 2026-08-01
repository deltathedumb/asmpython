# tier: spec
# ref: reference/expressions.html#value-comparisons
# expect:
# True
# True
# True
# True
# True
# True
# True
def ident(v):
    return v

for x in ['abc', 42, 3.5, 0, -1, True, False]:
    print(ident(x) == x)

# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# [1, 2] [1, 2]
# True
def add(v, into=[]):
    into.append(v)
    return into

first = add(1)
second = add(2)
print(first, second)
print(first is second)

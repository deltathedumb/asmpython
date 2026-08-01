# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# [1]
# [1, 2]
def collect(v, acc=[]):
    acc.append(v)
    return acc

print(collect(1))
print(collect(2))

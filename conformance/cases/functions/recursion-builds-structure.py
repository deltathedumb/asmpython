# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# [5, 4, 3, 2, 1]
# 50
def build(n):
    if n == 0:
        return []
    return [n] + build(n - 1)

print(build(5))
print(len(build(50)))

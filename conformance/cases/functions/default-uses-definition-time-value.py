# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# 1
# 5
n = 1

def f(v=n):
    return v

n = 99
print(f())
print(f(5))

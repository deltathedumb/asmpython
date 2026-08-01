# tier: cpython
# ref: reference/compound_stmts.html#function-definitions
# expect:
# 1
# 5
def make(n):
    def inner(v=n):
        return v
    n = 99
    return inner

f = make(1)
print(f())
print(f(5))

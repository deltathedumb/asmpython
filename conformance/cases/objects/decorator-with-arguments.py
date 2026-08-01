# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# ['x', 'x', 'x']
def repeat(n):
    def deco(fn):
        def inner(v):
            return [fn(v) for _ in range(n)]
        return inner
    return deco

@repeat(3)
def f(v):
    return v

print(f("x"))

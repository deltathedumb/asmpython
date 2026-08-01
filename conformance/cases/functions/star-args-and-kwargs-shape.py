# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# ((1, 2), [('a', 4), ('b', 3)])
# ((), [])
# tuple
def f(*a, **kw):
    return (a, sorted(kw.items()))

print(f(1, 2, b=3, a=4))
print(f())
print(type(f()[0]).__name__)

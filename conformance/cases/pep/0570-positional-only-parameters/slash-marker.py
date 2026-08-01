# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# (1, 2, 3, 4)
# (1, 2, 3, 4)
# TypeError
# (1, ['a'])
def f(a, b, /, c, *, d):
    return (a, b, c, d)

print(f(1, 2, 3, d=4))
print(f(1, 2, c=3, d=4))
try:
    f(a=1, b=2, c=3, d=4)
except TypeError:
    print("TypeError")

def g(a, /, **kw):
    return (a, sorted(kw))

print(g(1, a=2))

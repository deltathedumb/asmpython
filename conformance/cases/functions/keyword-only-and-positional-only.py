# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# (1, 2, 3)
# (1, 2, 3)
# TypeError
def f(a, /, b, *, c):
    return (a, b, c)

print(f(1, 2, c=3))
print(f(1, b=2, c=3))
try:
    f(1, 2, 3)
except TypeError:
    print("TypeError")

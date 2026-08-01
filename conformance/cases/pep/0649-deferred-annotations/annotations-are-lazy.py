# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# min-python: 3.14
# expect:
# True
# 1
# True
# NameError
def f(x: Undefined) -> AlsoUndefined:
    return x

print(callable(f))
print(f(1))
print(hasattr(f, "__annotate__"))
try:
    f.__annotations__
except NameError:
    print("NameError")

# tier: spec
# ref: reference/simple_stmts.html#the-pass-statement
# expect:
# None None
# C
# True
# Ellipsis
def f():
    pass

def g():
    ...

class C:
    pass

print(f(), g())
print(C().__class__.__name__)
print(... is Ellipsis)
print(repr(...))

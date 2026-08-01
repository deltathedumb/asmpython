# tier: spec
# ref: reference/simple_stmts.html#the-return-statement
# expect:
# None
# None
# True
def f():
    pass

def g():
    return

print(f())
print(g())
print(f() is None)

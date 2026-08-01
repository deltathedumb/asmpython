# tier: cpython
# ref: reference/compound_stmts.html#function-definitions
# expect:
# second
# second
def f():
    return "first"

def f():
    return "second"

print(f())

class C:
    def m(self):
        return "first"
    def m(self):
        return "second"

print(C().m())

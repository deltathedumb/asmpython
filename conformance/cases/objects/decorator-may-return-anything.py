# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# not-a-function
# str
# g
def to_string(fn):
    return "not-a-function"

@to_string
def f():
    pass

print(f)
print(type(f).__name__)

def to_class(fn):
    class Wrapper:
        name = fn.__name__
    return Wrapper

@to_class
def g():
    pass

print(g.name)

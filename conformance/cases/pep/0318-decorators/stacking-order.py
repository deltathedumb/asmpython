# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# ['inner', 'outer']
log = []

def outer(fn):
    log.append("outer")
    return fn

def inner(fn):
    log.append("inner")
    return fn

@outer
@inner
def f():
    pass

print(log)

# tier: spec
# ref: reference/simple_stmts.html#the-nonlocal-statement
# expect:
# 2
def outer():
    n = 1
    def inner():
        nonlocal n
        n = 2
    inner()
    return n

print(outer())

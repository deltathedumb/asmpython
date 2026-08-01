# tier: spec
# ref: reference/compound_stmts.html#function-definitions
# expect:
# wrapped:6
# inner
def deco(fn):
    def inner(*a):
        return "wrapped:" + str(fn(*a))
    return inner

@deco
def f(n):
    return n * 2

print(f(3))
print(f.__name__)

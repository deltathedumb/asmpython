# tier: spec
# ref: reference/simple_stmts.html#the-return-statement
# expect:
# None None early
# True
def a():
    return

def b():
    pass

def c():
    for _ in range(1):
        return "early"

print(a(), b(), c())
print(a() is b() is None)

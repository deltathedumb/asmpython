# tier: spec
# ref: reference/compound_stmts.html#class-patterns
# expect:
# 99 big-int:99
# 3 int
# 'x' str:x
# [1, 2] pair:1,2
# 1.5 other
def f(v):
    match v:
        case int(n) if n > 10:
            return f"big-int:{n}"
        case int():
            return "int"
        case str(s):
            return f"str:{s}"
        case list([a, b]):
            return f"pair:{a},{b}"
        case _:
            return "other"

for v in (99, 3, "x", [1, 2], 1.5):
    print(repr(v), f(v))

# tier: spec
# ref: reference/compound_stmts.html#value-patterns
# expect:
# red green other
class Color:
    RED = 1
    GREEN = 2

def f(v):
    match v:
        case Color.RED:
            return "red"
        case Color.GREEN:
            return "green"
        case _:
            return "other"

print(f(1), f(2), f(3))

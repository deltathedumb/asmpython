# tier: spec
# ref: reference/compound_stmts.html#the-match-statement
# expect:
# ('bound', 1, 2)
# unbound
# unbound
def f(v):
    match v:
        case [x, y]:
            return ("bound", x, y)
        case _:
            return "unbound"

print(f([1, 2]))
print(f([1]))
print(f([1, 2, 3]))

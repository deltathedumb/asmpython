# tier: spec
# ref: reference/compound_stmts.html#the-match-statement
# expect:
# 0 zero
# 1 one-or-two
# 2 one-or-two
# s string
# None none
# True one-or-two
# 9 other
def f(v):
    match v:
        case 0:
            return "zero"
        case 1 | 2:
            return "one-or-two"
        case "s":
            return "string"
        case None:
            return "none"
        case True:
            return "true"
        case _:
            return "other"

for v in (0, 1, 2, "s", None, True, 9):
    print(v, f(v))

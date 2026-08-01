# tier: spec
# ref: reference/compound_stmts.html#guards
# expect:
# negative:-3
# zero
# even:4
# odd:5
def f(v):
    match v:
        case n if n < 0:
            return "negative:" + str(n)
        case 0:
            return "zero"
        case n if n % 2 == 0:
            return "even:" + str(n)
        case n:
            return "odd:" + str(n)

for v in (-3, 0, 4, 5):
    print(f(v))

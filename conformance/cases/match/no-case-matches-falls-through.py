# tier: spec
# ref: reference/compound_stmts.html#the-match-statement
# expect:
# ['one', ('after', 1), ('after', 2)]
log = []

def f(v):
    match v:
        case 1:
            log.append("one")
    log.append(("after", v))

f(1)
f(2)
print(log)

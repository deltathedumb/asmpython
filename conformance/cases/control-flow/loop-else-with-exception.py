# tier: spec
# ref: reference/compound_stmts.html#the-for-statement
# expect:
# [0, 'caught', 2, 'else']
log = []
for i in range(3):
    try:
        if i == 1:
            raise ValueError("x")
        log.append(i)
    except ValueError:
        log.append("caught")
else:
    log.append("else")
print(log)

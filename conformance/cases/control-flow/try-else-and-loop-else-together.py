# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# [('try-else', 0), ('try-else', 1), ('try-else', 2), 'for-else']
log = []
for i in range(3):
    try:
        if i == 5:
            raise ValueError
    except ValueError:
        log.append("except")
    else:
        log.append(("try-else", i))
else:
    log.append("for-else")
print(log)

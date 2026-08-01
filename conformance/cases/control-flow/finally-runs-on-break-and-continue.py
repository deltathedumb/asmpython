# tier: spec
# ref: reference/compound_stmts.html#the-try-statement
# expect:
# [('finally', 0), ('body', 1), ('finally', 1), ('finally', 2)]
log = []
for i in range(3):
    try:
        if i == 0:
            continue
        if i == 2:
            break
        log.append(("body", i))
    finally:
        log.append(("finally", i))
print(log)

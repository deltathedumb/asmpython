# tier: spec
# ref: reference/compound_stmts.html#the-break-statement
# expect:
# [(0, 0, 1), (1, 0, 1)]
log = []
for a in range(2):
    for b in range(2):
        for c in range(2):
            if c == 0:
                continue
            if b == 1:
                break
            log.append((a, b, c))
print(log)

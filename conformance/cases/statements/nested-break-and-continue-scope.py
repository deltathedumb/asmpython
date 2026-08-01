# tier: spec
# ref: reference/compound_stmts.html#the-break-statement
# expect:
# [(0, 0), (1, 0)]
# 3
log = []
for i in range(3):
    for j in range(3):
        if j == 1:
            break
        if i == 2:
            continue
        log.append((i, j))
print(log)

n = 0
while True:
    n += 1
    if n > 2:
        break
print(n)

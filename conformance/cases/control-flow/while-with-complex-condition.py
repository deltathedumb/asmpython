# tier: spec
# ref: reference/compound_stmts.html#the-while-statement
# expect:
# 5 [2, 4]
# [2, 4, 3, 2, 1]
log = []
n = 0
while n < 5 and len(log) < 3:
    n += 1
    if n % 2:
        continue
    log.append(n)
print(n, log)

items = [1, 2, 3]
while items:
    log.append(items.pop())
print(log)

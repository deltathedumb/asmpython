# tier: spec
# ref: reference/simple_stmts.html#assignment-statements
# expect:
# [0, 9, 3]
# [1, 3]
log = []

def probe(n):
    log.append(n)
    return n

xs = [0, 1, 2, 3]
xs[probe(1):probe(3)] = [9]
print(xs)
print(log)

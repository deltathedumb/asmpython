# tier: spec
# ref: reference/simple_stmts.html#augmented-assignment-statements
# expect:
# [15]
# ['idx']
log = []

def idx():
    log.append("idx")
    return 0

xs = [10]
xs[idx()] += 5
print(xs)
print(log)

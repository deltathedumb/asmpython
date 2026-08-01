# tier: spec
# ref: library/functions.html#filter
# expect:
# []
# [2, 4]
# [1, 2, 3, 4]
# [2, 4]
log = []

def keep(v):
    log.append(v)
    return v % 2 == 0

f = filter(keep, [1, 2, 3, 4])
print(log)
print(list(f))
print(log)
print(list(map(lambda v: v * 2, [1, 2])))

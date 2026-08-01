# tier: spec
# ref: reference/expressions.html#generator-expressions
# expect:
# ['outer']
# [2, 3, 3, 4]
# ['outer', 'inner', 'inner']
log = []

def source(tag):
    log.append(tag)
    return [1, 2]

g = (a + b for a in source("outer") for b in source("inner"))
print(log)
print(list(g))
print(log)

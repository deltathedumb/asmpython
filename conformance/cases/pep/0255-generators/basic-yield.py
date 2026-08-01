# tier: spec
# ref: peps.python.org/pep-0255/
# expect:
# [0, 1, 2]
# 0
# 1
def count(n):
    i = 0
    while i < n:
        yield i
        i += 1

print(list(count(3)))
g = count(2)
print(next(g))
print(next(g))

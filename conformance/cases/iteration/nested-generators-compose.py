# tier: spec
# ref: reference/expressions.html#yield-expressions
# expect:
# [0, 2, 4, 6]
# [0, 2]
# 6
def take(n, it):
    for i, v in enumerate(it):
        if i >= n:
            return
        yield v

def evens():
    n = 0
    while True:
        yield n
        n += 2

print(list(take(4, evens())))
print(list(take(2, take(4, evens()))))
print(sum(take(3, evens())))

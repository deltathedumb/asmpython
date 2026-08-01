# tier: spec
# ref: reference/expressions.html#yield-expressions
# expect:
# 1
# cleanup
# 1
# StopIteration done
def gen():
    try:
        yield 1
        yield 2
    finally:
        print("cleanup")

g = gen()
print(next(g))
g.close()

def with_return():
    yield 1
    return "done"

g2 = with_return()
print(next(g2))
try:
    next(g2)
except StopIteration as e:
    print("StopIteration", e.value)

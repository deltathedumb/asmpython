# tier: spec
# ref: reference/expressions.html#generator.throw
# expect:
# 1
# ValueError boom
# StopIteration
def gen():
    yield 1
    yield 2

g = gen()
print(next(g))
try:
    g.throw(ValueError("boom"))
except ValueError as e:
    print("ValueError", e)
try:
    next(g)
except StopIteration:
    print("StopIteration")

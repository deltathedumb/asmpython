# tier: spec
# ref: reference/expressions.html#generator-iterator-methods
# expect:
# 1
# RuntimeError
# StopIteration
def gen():
    yield 1
    raise StopIteration("inner")

g = gen()
print(next(g))
try:
    next(g)
except RuntimeError as e:
    print("RuntimeError")
    print(type(e.__cause__).__name__)

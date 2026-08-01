# tier: spec
# ref: reference/expressions.html#generator-iterator-methods
# expect:
# 1
# caught
# 1
# [2]
def gen():
    try:
        yield 1
        yield 2
    except ValueError:
        yield "caught"

g = gen()
print(next(g))
print(g.throw(ValueError("x")))
g2 = gen()
print(next(g2))
print(list(g2))

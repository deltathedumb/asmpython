# tier: spec
# ref: reference/simple_stmts.html#augmented-assignment-statements
# expect:
# 6
# {'k': [1, 2]}
# [[1, 2]]
# 1
class C:
    def __init__(self):
        self.n = 1

c = C()
c.n += 5
print(c.n)

d = {"k": [1]}
d["k"] += [2]
print(d)

xs = [[1]]
xs[0] += [2]
print(xs)

t = (1, 2)
n = 0
n += t[0]
print(n)

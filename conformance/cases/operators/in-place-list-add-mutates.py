# tier: spec
# ref: reference/datamodel.html#object.__iadd__
# expect:
# [1, 99]
# (1,)
def extend(xs):
    xs += [99]

xs = [1]
extend(xs)
print(xs)

def rebind(t):
    t += (99,)

t = (1,)
rebind(t)
print(t)

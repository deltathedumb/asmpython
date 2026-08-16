# probes: a container passed to a function is read there (float elements)
# expect:
# 4
# 1.5
def count(xs):
    total = 0
    for _ in xs:
        total = total + 1
    return total


def first_of(xs):
    return xs[0]


xs = [1.5, 2.5, 3.5, 4.5]
print(count(xs))
print(first_of(xs))

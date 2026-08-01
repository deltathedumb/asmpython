# probes: a container passed to a function is read there (int elements)
# expect:
# 4
# 10
def count(xs):
    total = 0
    for _ in xs:
        total = total + 1
    return total


def first_of(xs):
    return xs[0]


xs = [10, 20, 30, 40]
print(count(xs))
print(first_of(xs))

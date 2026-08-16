# probes: a container passed to a function is read there (mixed elements)
# expect:
# 5
# 1
def count(xs):
    total = 0
    for _ in xs:
        total = total + 1
    return total


def first_of(xs):
    return xs[0]


xs = [1, "two", 3.5, True, None]
print(count(xs))
print(first_of(xs))

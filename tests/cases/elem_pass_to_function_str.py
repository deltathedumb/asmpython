# probes: a container passed to a function is read there (str elements)
# expect:
# 4
# aa
def count(xs):
    total = 0
    for _ in xs:
        total = total + 1
    return total


def first_of(xs):
    return xs[0]


xs = ["aa", "bb", "cc", "dd"]
print(count(xs))
print(first_of(xs))

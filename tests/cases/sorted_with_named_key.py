# expect:
# [(2, 1), (3, 2), (1, 3)]
def by_second(pair):
    return pair[1]
data = [(1, 3), (2, 1), (3, 2)]
print(sorted(data, key=by_second))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (sorted key)

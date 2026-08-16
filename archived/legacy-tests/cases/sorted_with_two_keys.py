# expect:
# [('a', 1), ('a', 2), ('b', 2)]
data = [('b', 2), ('a', 2), ('a', 1)]
print(sorted(data, key=lambda x: (x[0], x[1])))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (sorted key lambda body)

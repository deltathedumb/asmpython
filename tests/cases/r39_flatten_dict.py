# expect:
# [('a.b', 1), ('a.c', 2), ('d.e', 3)]
nested = {'a': {'b': 1, 'c': 2}, 'd': {'e': 3}}
flat = {}
for outer, inner in nested.items():
    for key, val in inner.items():
        flat[outer + '.' + key] = val
print(sorted(flat.items()))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

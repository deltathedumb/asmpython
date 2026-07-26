# expect:
# b
d = {'a': 1, 'b': 2, 'c': 3}
inv = {}
for k, v in d.items():
    inv[v] = k
print(inv[2])
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# ['z', 'a', 'm', 'b']
# ['z', 'a', 'm', 'b']
# [1, 1, 1, 1]
# ['z', 'a', 'm', 'b']
d = {}
for k in ['z', 'a', 'm', 'b']:
    d[k] = len(k)
print(list(d))
print(list(d.keys()))
print(list(d.values()))
d['z'] = 99
print(list(d))

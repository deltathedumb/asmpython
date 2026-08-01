# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# 1
# text
# 2.5
# False
# None
d = {'i': 1, 's': 'text', 'f': 2.5, 'b': False, 'n': None}
for k in ['i', 's', 'f', 'b', 'n']:
    print(d[k])

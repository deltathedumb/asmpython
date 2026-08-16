# expect:
# ['a', 'c']
d = {'a': 1, 'b': 2, 'c': 1}
inverted = {}
for k, v in d.items():
    inverted.setdefault(v, []).append(k)
print(sorted(inverted[1]))
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'append'

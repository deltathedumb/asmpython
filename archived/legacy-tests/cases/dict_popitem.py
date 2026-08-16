# expect:
# a 1
d = {'a': 1}
k, v = d.popitem()
print(k, v)
# asmpython (beta/3.14.0) rejects at compile: [E113] dict has no method 'popitem'

# expect:
# [('a', 5), ('b', 5)]
d = {}
d['a'] = d['b'] = 5
print(sorted(d.items()))
# asmpython (beta/3.14.0) rejects at compile: [P002] expected NEWLINE, got OP '='

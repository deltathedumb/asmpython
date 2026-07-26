# expect:
# 1 [2, 3, 4]
a, *rest = 1, 2, 3, 4
print(a, rest)
# asmpython (beta/3.14.0) rejects at compile: [E114] starred assignment requires a single list on the right-hand side

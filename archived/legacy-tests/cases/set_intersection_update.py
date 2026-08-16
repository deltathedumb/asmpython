# expect:
# [2, 3]
s = {1, 2, 3, 4}
s.intersection_update({2, 3, 5})
print(sorted(s))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr MethodCall (set.intersection_update)

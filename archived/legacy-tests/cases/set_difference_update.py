# expect:
# [1, 3]
s = {1, 2, 3, 4}
s.difference_update({2, 4})
print(sorted(s))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr MethodCall (set.difference_update)

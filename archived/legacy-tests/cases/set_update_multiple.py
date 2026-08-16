# expect:
# [1, 2, 3, 4, 5]
s = {1}
s.update([2, 3], [4, 5])
print(sorted(s))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr MethodCall (set.update)

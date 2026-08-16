# expect:
# True
print({1, 2, 3}.issuperset({1, 2}))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr MethodCall (set.issuperset)

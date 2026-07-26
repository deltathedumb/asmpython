# expect:
# True False
print({1, 2}.isdisjoint({3, 4}), {1, 2}.isdisjoint({2, 3}))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr MethodCall (set.isdisjoint)

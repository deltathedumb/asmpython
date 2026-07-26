# expect:
# [1, 4, 5]
a = [1, 2, 3, 4, 5]
del a[1:3]
print(a)
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Slice

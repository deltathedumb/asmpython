# expect:
# [10, 1, 20, 3, 30, 5]
a = [0, 1, 2, 3, 4, 5]
a[::2] = [10, 20, 30]
print(a)
# asmpython (beta/3.14.0) rejects at compile: unsupported stmt IndexAssign (slice step)

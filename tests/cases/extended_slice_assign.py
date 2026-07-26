# expect:
# [0, 1, 20, 3, 40, 5, 60, 7, 8, 9]
a = list(range(10))
a[2:8:2] = [20, 40, 60]
print(a)
# asmpython (beta/3.14.0) rejects at compile: unsupported stmt IndexAssign (slice step)

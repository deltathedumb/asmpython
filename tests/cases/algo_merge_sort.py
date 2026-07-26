# expect:
# [1, 2, 3, 4, 7, 9]
def merge(a, b):
    r = []
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            r.append(a[i])
            i += 1
        else:
            r.append(b[j])
            j += 1
    return r + a[i:] + b[j:]
def msort(xs):
    if len(xs) <= 1:
        return xs
    m = len(xs) // 2
    return merge(msort(xs[:m]), msort(xs[m:]))
print(msort([4, 1, 7, 3, 9, 2]))
# asmpython (beta/3.14.0) rejects at compile: unsupported stmt MultiAssign

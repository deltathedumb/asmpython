# expect:
# 5.0 5.0
def stats(data):
    n = len(data)
    mean = sum(data) / n
    var = sum((x - mean) ** 2 for x in data) / n
    return mean, var
m, v = stats([2, 4, 6, 8])
print(m, v)
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Comprehension (float element)

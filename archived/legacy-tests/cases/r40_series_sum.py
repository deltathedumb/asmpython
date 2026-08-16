# expect:
# 2.0833
def series_sum(n):
    return sum(1 / i for i in range(1, n + 1))
print(round(series_sum(4), 4))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Comprehension (float element)

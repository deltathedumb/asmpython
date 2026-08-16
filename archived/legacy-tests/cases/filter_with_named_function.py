# expect:
# [3, 5]
def is_positive(x):
    return x > 0
print(list(filter(is_positive, [-2, 3, -1, 5])))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (filter() with a non-lambda predicate)

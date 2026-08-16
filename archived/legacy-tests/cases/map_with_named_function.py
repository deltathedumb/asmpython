# expect:
# [1, 8, 27]
def cube(x):
    return x ** 3
print(list(map(cube, [1, 2, 3])))
# asmpython (beta/3.14.0) rejects at compile: unsupported expr Call (map() with a non-lambda predicate)

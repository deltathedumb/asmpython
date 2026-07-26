# expect:
# 12
def multiply(a, b):
    return a * b
def partial(fn, a):
    return lambda b: fn(a, b)
times3 = partial(multiply, 3)
print(times3(4))
# asmpython (beta/3.14.0) rejects at compile: [E002] undefined function 'fn'

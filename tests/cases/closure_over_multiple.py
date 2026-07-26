# expect:
# 11 20
def make_ops(x):
    return (lambda: x + 1, lambda: x * 2)
add, mul = make_ops(10)
print(add(), mul())
# asmpython (beta/3.14.0) rejects at compile: [E001] undefined variable 'x'

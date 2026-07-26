# expect:
# 5
def logged(f):
    def wrap(*a, **k):
        return f(*a, **k)
    return wrap
@logged
def add(a, b):
    return a + b
print(add(2, 3))
# asmpython (beta/3.14.0) rejects at compile: [E023] *expr argument unpacking requires a tuple with known element types

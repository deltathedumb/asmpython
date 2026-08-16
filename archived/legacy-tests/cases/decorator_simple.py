# expect:
# 11
def deco(f):
    def wrap(*a):
        return f(*a) + 1
    return wrap
@deco
def g(x):
    return x * 2
print(g(5))
# asmpython (beta/3.14.0) rejects at compile: [E023] *expr argument unpacking requires a tuple with known element types

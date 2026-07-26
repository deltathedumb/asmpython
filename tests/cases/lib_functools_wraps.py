# expect:
# 5
from functools import wraps
def deco(f):
    @wraps(f)
    def w(*a):
        return f(*a)
    return w
@deco
def g(x):
    return x + 1
print(g(4))
# asmpython (beta/3.14.0) rejects at compile: [P001] unexpected token OP '@'

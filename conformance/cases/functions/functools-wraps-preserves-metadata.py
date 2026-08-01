# tier: spec
# ref: library/functools.html#functools.wraps
# expect:
# documented
# The docstring.
# 1
# documented
import functools

def deco(fn):
    @functools.wraps(fn)
    def inner(*a, **kw):
        return fn(*a, **kw)
    return inner

@deco
def documented(x):
    """The docstring."""
    return x

print(documented.__name__)
print(documented.__doc__)
print(documented(1))
print(documented.__wrapped__.__name__)

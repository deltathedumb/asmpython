# probes: functools.wraps keeps __name__
# expect:
# original
# Doc text.
# 1
import functools


def trace(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


@trace
def original(a):
    """Doc text."""
    return a


print(original.__name__)
print(original.__doc__)
print(original(1))

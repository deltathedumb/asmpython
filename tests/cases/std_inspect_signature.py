# probes: inspect.signature renders a parameter list
# expect:
# (a, b=1, *rest, key=None)
import inspect


def sample(a, b=1, *rest, key=None):
    return a


print(str(inspect.signature(sample)))

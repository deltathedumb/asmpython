# tier: spec
# ref: reference/datamodel.html#user-defined-functions
# expect:
# f Doc.
# (1,) {'c': 2}
# 2 ('a', 'b')
# attached
# f
def f(a, b=1, *args, c=2, **kw):
    """Doc."""
    return a

print(f.__name__, f.__doc__)
print(f.__defaults__, f.__kwdefaults__)
print(f.__code__.co_argcount, f.__code__.co_varnames[:2])
f.custom = "attached"
print(f.custom)
print(f.__qualname__)

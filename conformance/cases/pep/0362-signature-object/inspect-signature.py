# tier: spec
# ref: library/inspect.html#inspect.signature
# expect:
# (a, b=1, *args, c, **kw)
# ['a', 'b', 'args', 'c', 'kw']
# 1
# KEYWORD_ONLY
import inspect

def f(a, b=1, *args, c, **kw):
    pass

sig = inspect.signature(f)
print(str(sig))
print(list(sig.parameters))
print(sig.parameters["b"].default)
print(sig.parameters["c"].kind.name)

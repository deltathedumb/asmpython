# tier: spec
# ref: reference/expressions.html#calls
# expect:
# (1, 2, (), 3, [])
# (1, 2, (3, 4), 5, ['extra'])
# (1, 2, (), 3, [])
# TypeError
def f(pos, /, normal, *args, kwonly, **kw):
    return (pos, normal, args, kwonly, sorted(kw))

print(f(1, 2, kwonly=3))
print(f(1, 2, 3, 4, kwonly=5, extra=6))
print(f(1, normal=2, kwonly=3))
try:
    f(pos=1, normal=2, kwonly=3)
except TypeError:
    print("TypeError")

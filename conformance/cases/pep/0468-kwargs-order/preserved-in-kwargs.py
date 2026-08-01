# tier: spec
# ref: peps.python.org/pep-0468/
# expect:
# ['z', 'a', 'm']
# ['b', 'a']
def f(**kw):
    return list(kw)

print(f(z=1, a=2, m=3))
print(f(b=1, a=2))

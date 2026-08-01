# tier: spec
# ref: peps.python.org/pep-0448/
# expect:
# 6
# 6
# [1, 2, 3, 4]
# {'a': 1, 'b': 2}
# (1, 2, 3)
def f(a, b, c):
    return a + b + c

args = [1, 2]
print(f(*args, 3))
kw = {'b': 2, 'c': 3}
print(f(1, **kw))
print([*[1, 2], 3, *[4]])
print({**{'a': 1}, 'b': 2})
print((*[1, 2], 3))

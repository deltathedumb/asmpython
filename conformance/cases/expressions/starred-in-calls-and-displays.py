# tier: spec
# ref: reference/expressions.html#calls
# expect:
# (1, 2, 3)
# (1, 2, 3)
# [1, 2, 3]
# True
# {'a': 1, 'b': 2}
# (1, 2, 3)
def f(a, b, c):
    return (a, b, c)

args = [1, 2]
print(f(*args, 3))
print(f(**{"a": 1, "b": 2, "c": 3}))
print([*args, 3])
print({*args, 3} == {1, 2, 3})
print({**{"a": 1}, "b": 2})
print((*args, 3))

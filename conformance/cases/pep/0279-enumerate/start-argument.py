# tier: spec
# ref: peps.python.org/pep-0279/
# expect:
# 0 a
# 1 b
# 2 c
# 1 a
# 2 b
# 3 c
# 10 a
# 11 b
# 12 c
xs = ['a', 'b', 'c']
for i, v in enumerate(xs):
    print(i, v)
for i, v in enumerate(xs, 1):
    print(i, v)
for i, v in enumerate(xs, start=10):
    print(i, v)

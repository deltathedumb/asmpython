# probes: a generator yields lazily in order
# expect:
# 1
# 2
# 3
def counter():
    yield 1
    yield 2
    yield 3


for v in counter():
    print(v)

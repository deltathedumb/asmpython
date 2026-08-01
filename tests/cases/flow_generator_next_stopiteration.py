# probes: an exhausted generator raises StopIteration
# expect:
# 1
# stopped
def one():
    yield 1


gen = one()
print(next(gen))
try:
    next(gen)
    print("no stop")
except StopIteration:
    print("stopped")

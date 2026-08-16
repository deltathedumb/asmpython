# probes: a generator's return value rides on StopIteration
# expect:
# 1
# final
def with_result():
    yield 1
    return "final"


gen = with_result()
print(next(gen))
try:
    next(gen)
except StopIteration as stop:
    print(stop.value)

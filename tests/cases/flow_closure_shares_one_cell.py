# probes: two closures over one variable share it
# expect:
# 42
def make_pair():
    value = 0

    def setter(v):
        nonlocal value
        value = v

    def getter():
        return value

    return setter, getter


put, take = make_pair()
put(42)
print(take())

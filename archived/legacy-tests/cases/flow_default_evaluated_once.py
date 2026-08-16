# probes: a mutable default is shared between calls
# expect:
# [1]
# [1, 2]
def accumulate(value, into=[]):
    into.append(value)
    return into


print(accumulate(1))
print(accumulate(2))

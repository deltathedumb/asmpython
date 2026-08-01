# probes: a mutable default persists between calls
# expect:
# [1]
# [1, 2]
# [1, 2, 3]
def collect(value, into=[]):
    into.append(value)
    return into


print(collect(1))
print(collect(2))
print(collect(3))

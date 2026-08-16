# probes: the None-sentinel idiom gives a fresh list
# expect:
# [1]
# [2]
def accumulate(value, into=None):
    if into is None:
        into = []
    into.append(value)
    return into


print(accumulate(1))
print(accumulate(2))

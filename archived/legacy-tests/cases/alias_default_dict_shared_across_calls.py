# probes: a mutable default dict persists between calls
# expect:
# 1
# 2
def record(key, into={}):
    into[key] = len(into)
    return len(into)


print(record("a"))
print(record("b"))

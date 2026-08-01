# probes: every call sees the same default object
# expect:
# True
def get_default(into=[]):
    return into


print(get_default() is get_default())

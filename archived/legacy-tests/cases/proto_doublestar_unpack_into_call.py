# probes: **mapping spreads into keywords
# expect:
# x=2
def describe(name, count):
    return name + "=" + str(count)


print(describe(**{"name": "x", "count": 2}))

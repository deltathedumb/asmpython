# probes: a list may hold mixed kinds
# expect:
# 1
# two
# 3.0
# True
# None
items = [1, "two", 3.0, True, None]
for v in items:
    print(v)

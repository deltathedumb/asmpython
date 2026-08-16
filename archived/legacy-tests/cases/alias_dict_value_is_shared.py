# probes: a list stored as a dict value stays shared
# expect:
# [1, 2]
items = [1]
holder = {"items": items}
items.append(2)
print(holder["items"])

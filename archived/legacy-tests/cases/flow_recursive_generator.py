# probes: a generator may delegate to itself
# expect:
# [1, 2, 3, 4, 5]
def flatten(items):
    for item in items:
        if isinstance(item, list):
            yield from flatten(item)
        else:
            yield item


print(list(flatten([1, [2, [3, 4]], 5])))

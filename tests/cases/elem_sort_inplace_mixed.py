# probes: list.sort orders in place (mixed elements)
# expect:
# sort refused
xs = list([1, "two", 3.5, True, None])
try:
    xs.sort()
    print("sort returned")
except TypeError:
    print("sort refused")

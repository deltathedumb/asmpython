# probes: min/max order the elements (mixed elements)
# expect:
# min refused
xs = [1, "two", 3.5, True, None]
try:
    print(min(xs))
    print("min returned a value")
except TypeError:
    print("min refused")

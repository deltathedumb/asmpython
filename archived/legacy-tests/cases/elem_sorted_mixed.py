# probes: sorted() orders the elements (mixed elements)
# expect:
# sorted refused
xs = [1, "two", 3.5, True, None]
try:
    print(sorted(xs))
    print("sorted returned a value")
except TypeError:
    print("sorted refused")

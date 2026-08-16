# probes: `in` compares against each element (mixed elements)
# expect:
# True
# False
# True
xs = [1, "two", 3.5, True, None]
print("two" in xs)
print("zz" in xs)
print("zz" not in xs)

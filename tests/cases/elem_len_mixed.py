# probes: len() over the container (mixed elements)
# expect:
# 5
# True
xs = [1, "two", 3.5, True, None]
print(len(xs))
print(len(xs) > 0)

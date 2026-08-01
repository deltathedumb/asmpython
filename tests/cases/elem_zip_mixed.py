# probes: zip() reads elements from two containers (mixed elements)
# expect:
# 1 1
# two two
# 3.5 3.5
# True True
# None None
xs = [1, "two", 3.5, True, None]
ys = [1, "two", 3.5, True, None]
for a, b in zip(xs, ys):
    print(a, b)

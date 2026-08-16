# probes: a for target destructures each element (mixed elements)
# expect:
# 1 two
# three 4.5
pairs = [(1, "two"), ("three", 4.5)]
for left, right in pairs:
    print(left, right)

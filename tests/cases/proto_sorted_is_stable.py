# probes: sorted preserves the order of equal keys
# expect:
# [('a', 2), ('a', 4), ('b', 1), ('b', 3)]
pairs = [("b", 1), ("a", 2), ("b", 3), ("a", 4)]
print(sorted(pairs, key=lambda p: p[0]))

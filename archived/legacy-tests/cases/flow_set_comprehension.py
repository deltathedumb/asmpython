# probes: a set comprehension removes duplicates
# expect:
# [0, 1, 2]
print(sorted({v % 3 for v in range(7)}))

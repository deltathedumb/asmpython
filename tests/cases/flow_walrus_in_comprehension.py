# probes: a walrus binding in a comprehension outlives it
# expect:
# [10, 6]
# 6
values = [1, 5, 3]
kept = [seen for v in values if (seen := v * 2) > 4]
print(kept)
print(seen)

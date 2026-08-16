# probes: a comprehension filters with its if clause
# expect:
# [0, 2, 4]
print([v for v in range(6) if v % 2 == 0])

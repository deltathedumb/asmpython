# probes: a for target destructures each element (float elements)
# expect:
# 1.5 2.5
# 3.5 4.5
pairs = [(1.5, 2.5), (3.5, 4.5)]
for left, right in pairs:
    print(left, right)

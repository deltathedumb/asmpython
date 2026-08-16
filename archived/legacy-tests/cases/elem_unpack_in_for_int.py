# probes: a for target destructures each element (int elements)
# expect:
# 10 20
# 30 40
pairs = [(10, 20), (30, 40)]
for left, right in pairs:
    print(left, right)

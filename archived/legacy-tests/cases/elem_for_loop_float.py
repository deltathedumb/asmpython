# probes: for-loop iteration reads each element (float elements)
# expect:
# 1.5
# 2.5
# 3.5
# 4.5
xs = [1.5, 2.5, 3.5, 4.5]
for v in xs:
    print(v)

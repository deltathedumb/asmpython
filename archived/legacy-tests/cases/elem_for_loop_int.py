# probes: for-loop iteration reads each element (int elements)
# expect:
# 10
# 20
# 30
# 40
xs = [10, 20, 30, 40]
for v in xs:
    print(v)

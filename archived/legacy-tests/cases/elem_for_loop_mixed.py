# probes: for-loop iteration reads each element (mixed elements)
# expect:
# 1
# two
# 3.5
# True
# None
xs = [1, "two", 3.5, True, None]
for v in xs:
    print(v)

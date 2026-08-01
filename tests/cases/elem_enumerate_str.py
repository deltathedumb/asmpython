# probes: enumerate() reads each element (str elements)
# expect:
# 0 aa
# 1 bb
# 2 cc
# 3 dd
xs = ["aa", "bb", "cc", "dd"]
for i, v in enumerate(xs):
    print(i, v)

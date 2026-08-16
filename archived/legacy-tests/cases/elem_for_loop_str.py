# probes: for-loop iteration reads each element (str elements)
# expect:
# aa
# bb
# cc
# dd
xs = ["aa", "bb", "cc", "dd"]
for v in xs:
    print(v)

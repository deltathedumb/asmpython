# probes: min/max with key= read each element (str elements)
# expect:
# aa
# dd
xs = ["aa", "bb", "cc", "dd"]
print(min(xs, key=str))
print(max(xs, key=str))

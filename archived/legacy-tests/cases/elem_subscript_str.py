# probes: a container element is read by literal index (str elements)
# expect:
# aa
# bb
# dd
xs = ["aa", "bb", "cc", "dd"]
print(xs[0])
print(xs[1])
print(xs[-1])

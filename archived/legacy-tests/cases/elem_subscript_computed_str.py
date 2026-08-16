# probes: a container element is read by computed index (str elements)
# expect:
# bb
# cc
# dd
xs = ["aa", "bb", "cc", "dd"]
i = 1
print(xs[i])
print(xs[i + 1])
print(xs[len(xs) - 1])

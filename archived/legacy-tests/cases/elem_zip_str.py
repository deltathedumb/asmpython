# probes: zip() reads elements from two containers (str elements)
# expect:
# aa aa
# bb bb
# cc cc
# dd dd
xs = ["aa", "bb", "cc", "dd"]
ys = ["aa", "bb", "cc", "dd"]
for a, b in zip(xs, ys):
    print(a, b)

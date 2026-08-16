# probes: len() over the container (str elements)
# expect:
# 4
# True
xs = ["aa", "bb", "cc", "dd"]
print(len(xs))
print(len(xs) > 0)

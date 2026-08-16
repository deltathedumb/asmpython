# probes: list.sort sorts in place and returns None
# expect:
# [1, 2, 3]
# None
xs = [3, 1, 2]
result = xs.sort()
print(xs)
print(result)

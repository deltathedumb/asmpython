# probes: a cycle is reachable at arbitrary depth
# expect:
# True
a = []
a.append(a)
print(a[0][0][0] is a)

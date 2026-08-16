# probes: a shallow copy still shares the inner objects
# expect:
# [1, 2]
# 2
inner = [1]
a = [inner]
b = a[:]
b[0].append(2)
print(a[0])
print(len(a[0]))

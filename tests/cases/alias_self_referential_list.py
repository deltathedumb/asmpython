# probes: a list containing itself renders as [...]
# expect:
# 2
# True
# [1, [...]]
a = [1]
a.append(a)
print(len(a))
print(a[1] is a)
print(a)

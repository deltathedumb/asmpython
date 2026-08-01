# probes: a full slice produces an independent list
# expect:
# [1, 2]
# [1, 2, 3]
# False
a = [1, 2]
b = a[:]
b.append(3)
print(a)
print(b)
print(a == b)

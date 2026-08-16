# probes: list.copy produces an independent list
# expect:
# [1, 2]
# [1, 2, 3]
a = [1, 2]
b = a.copy()
b.append(3)
print(a)
print(b)

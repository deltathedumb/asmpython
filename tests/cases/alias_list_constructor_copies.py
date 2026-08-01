# probes: list(a) produces an independent list
# expect:
# [1, 2]
# [1, 2, 3]
a = [1, 2]
b = list(a)
b.append(3)
print(a)
print(b)

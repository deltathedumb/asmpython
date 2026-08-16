# probes: set(a) produces an independent set
# expect:
# 2
# 3
a = {1, 2}
b = set(a)
b.add(3)
print(len(a))
print(len(b))

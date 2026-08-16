# probes: a class attribute is shared until shadowed
# expect:
# 5
# 5
# 9
# 5
# 5
class Counter:
    total = 0


a = Counter()
b = Counter()
Counter.total = 5
print(a.total)
print(b.total)
a.total = 9
print(a.total)
print(b.total)
print(Counter.total)

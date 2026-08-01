# probes: CPython caches small ints (implementation detail)
# expect:
# True
# True
# True
# False
a = 1
b = int("1")
print(a == b)
print(a is b)

big = 1000
other = int("1000")
print(big == other)
print(big is other)

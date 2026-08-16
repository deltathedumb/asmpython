# probes: a = b = value binds both names
# expect:
# [1, 2]
# [1, 2]
# True
a = b = [1]
a.append(2)
print(a)
print(b)
print(a is b)

# tier: spec
# ref: library/stdtypes.html#mutable-sequence-types
# expect:
# [1, [2, 3]]
# [1, 2, 3]
# [1, 'a', 'b']
# [1, 'a', 'b']
a = [1]
a.append([2, 3])
print(a)
b = [1]
b.extend([2, 3])
print(b)
c = [1]
c += "ab"
print(c)
d = [1]
d.extend("ab")
print(d)

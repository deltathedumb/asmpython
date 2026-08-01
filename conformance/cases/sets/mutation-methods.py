# tier: spec
# ref: library/stdtypes.html#set
# expect:
# [2, 3]
# [2, 3, 4, 5]
# True
# KeyError
s = {1, 2}
s.add(3)
s.discard(1)
s.discard(99)
print(sorted(s))
s.update([4, 5])
print(sorted(s))
print(s.pop() in {2, 3, 4, 5})
try:
    {1}.remove(9)
except KeyError:
    print("KeyError")

# expect:
# True
# False
# True
# False
# True
# False
# True
# False

s = {1, 2, 3}
print(2 in s)
print(5 in s)
s.add(4)
print(4 in s)
s.discard(2)
print(2 in s)
s2 = set([10, 20, 30])
print(20 in s2)
print(99 in s2)
nums = {x for x in range(5)}
print(2 in nums)
print(9 in nums)

# tier: spec
# ref: library/stdtypes.html#comparisons
# expect:
# True
# True
# False
# True
# True
print([1, 2] < [1, 3])
print([1, 2] < [1, 2, 0])
print([2] < [1, 9])
print([] < [0])
print([1, 2] == [1, 2])

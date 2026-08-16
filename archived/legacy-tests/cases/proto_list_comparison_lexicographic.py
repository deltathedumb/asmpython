# probes: lists compare element by element
# expect:
# True
# True
# True
print([1, 2] < [1, 3])
print([1, 2] < [1, 2, 0])
print([2] > [1, 9, 9])

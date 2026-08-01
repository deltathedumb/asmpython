# tier: spec
# ref: library/functions.html#zip
# expect:
# [(1, 'a'), (2, 'b')]
# []
# [(1, 2, 3)]
print(list(zip([1, 2, 3], "ab")))
print(list(zip()))
print(list(zip([1], [2], [3])))

# tier: spec
# ref: library/functions.html#enumerate
# expect:
# [(0, 'a'), (1, 'b'), (2, 'c')]
# [(1, 'a'), (2, 'b'), (3, 'c')]
# ['c', 'b', 'a']
# ['a', 'b', 'c']
xs = ["a", "b", "c"]
print(list(enumerate(xs)))
print(list(enumerate(xs, 1)))
print(list(reversed(xs)))
print(xs)

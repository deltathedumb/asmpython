# tier: spec
# ref: library/itertools.html
# expect:
# [1, 2, 3]
# [2, 3, 4]
# [5, 6, 7]
# ['x', 'x', 'x']
# [[1, 1], [2]]
# [(1, 'a'), (1, 'b'), (2, 'a'), (2, 'b')]
# [(1, 2), (1, 3), (2, 3)]
import itertools

print(list(itertools.chain([1], [2, 3])))
print(list(itertools.islice(range(10), 2, 5)))
print(list(itertools.count(5)[:0] if False else itertools.islice(itertools.count(5), 3)))
print(list(itertools.repeat("x", 3)))
print([list(g) for _, g in itertools.groupby([1, 1, 2])])
print(list(itertools.product([1, 2], "ab")))
print(list(itertools.combinations([1, 2, 3], 2)))

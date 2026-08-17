# COVERAGE: count, repeat, chain, islice, groupby, product, combinations.
# NOT covered: accumulate, batched, compress, cycle, dropwhile, filterfalse,
# pairwise, permutations, starmap, takewhile, tee, zip_longest.
import itertools

print(list(itertools.islice(itertools.count(), 5)))
print(list(itertools.islice(itertools.count(10), 3)))
print(list(itertools.islice(itertools.count(0, 3), 4)))
print(list(itertools.repeat("x", 3)))
print(list(itertools.chain([1, 2], (3,), "ab")))
print(list(itertools.chain()))
print(list(itertools.islice([0, 1, 2, 3, 4, 5], 2, 5)))
print(list(itertools.islice([0, 1, 2, 3, 4, 5], 1, 6, 2)))
print(list(itertools.product([1, 2], "ab")))
print(list(itertools.product([], "ab")))
print(list(itertools.product([1, 2], repeat=2)))
print(list(itertools.combinations([1, 2, 3], 2)))
print(list(itertools.combinations("abcd", 3)))
print(list(itertools.combinations([1, 2], 3)))
# GROUPBY GROUPS RUNS, not equal values anywhere in the sequence -- the
# classic mistake, so an unsorted input is used deliberately.
for key, grp in itertools.groupby([1, 1, 2, 2, 1]):
    print(key, list(grp))
for key, grp in itertools.groupby(["a", "bb", "cc", "d"], len):
    print(key, list(grp))

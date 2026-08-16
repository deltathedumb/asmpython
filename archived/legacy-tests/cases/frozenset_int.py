# expect:
# [1, 2, 3]
print(sorted(frozenset([3, 1, 2])))
# asmpython (beta/3.14.0) prints ['1', '2', '3']: same int-set element typing
# bug via frozenset().

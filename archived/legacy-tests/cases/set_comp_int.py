# expect:
# [0, 1, 2]
print(sorted({x % 3 for x in range(10)}))
# asmpython (beta/3.14.0) prints ['0', '1', '2']: same int-set element typing
# bug via the set-comprehension path.

# expect:
# [1, 2]
s = set()
s.add(1)
s.add(2)
print(sorted(s))
# asmpython (beta/3.14.0) prints ['1', '2']: same int-set element typing bug
# via set().add() (the mutation path, distinct from set literals).

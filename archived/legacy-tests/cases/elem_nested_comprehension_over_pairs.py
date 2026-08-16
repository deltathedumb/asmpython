# probes: a comprehension over a list of tuples reads both halves
# expect:
# ['a', 'b']
# {1: 'a', 2: 'b'}
rows = [(1, "a"), (2, "b")]
print([label for _, label in rows])
print({number: label for number, label in rows})

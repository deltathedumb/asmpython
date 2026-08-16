# probes: tuples compare element by element
# expect:
# True
# [(1, 'a'), (1, 'c'), (2, 'b')]
print((1, 2) < (1, 3))
print(sorted([(2, "b"), (1, "c"), (1, "a")]))

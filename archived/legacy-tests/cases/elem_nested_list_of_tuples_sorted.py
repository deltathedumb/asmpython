# probes: sorting a list of tuples orders by the first element
# expect:
# [(1, 'a'), (1, 'c'), (2, 'b')]
rows = [(2, "b"), (1, "c"), (1, "a")]
print(sorted(rows))

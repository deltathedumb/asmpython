# probes: a list of pair tuples converts to a dict
# expect:
# {1: 'a', 2: 'b'}
# a
rows = [(1, "a"), (2, "b")]
built = dict(rows)
print(built)
print(built[1])

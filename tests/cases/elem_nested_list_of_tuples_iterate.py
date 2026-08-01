# probes: iterating a list of tuples yields intact tuples
# expect:
# (1, 'a')
# a
# (2, 'b')
# b
rows = [(1, "a"), (2, "b")]
for row in rows:
    print(row)
    print(row[1])

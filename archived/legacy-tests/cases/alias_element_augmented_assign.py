# probes: xs[0] += mutates in place through the subscript
# expect:
# [6]
# [[1, 2]]
counts = [1]
counts[0] += 5
print(counts)

rows = [[1]]
rows[0] += [2]
print(rows)

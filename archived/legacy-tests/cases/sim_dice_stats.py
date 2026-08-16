# expect:
# most common: 6 appeared 3
rolls = [3, 6, 1, 4, 6, 2, 5, 6]
counts = {}
for r in rolls:
    counts[r] = counts.get(r, 0) + 1
most = max(counts, key=lambda k: counts[k])
print('most common:', most, 'appeared', counts[most])
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

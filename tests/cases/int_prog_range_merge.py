# expect:
# [(1, 6), (8, 10)]
intervals = [(1, 3), (2, 6), (8, 10)]
intervals.sort()
merged = [intervals[0]]
for start, end in intervals[1:]:
    if start <= merged[-1][1]:
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    else:
        merged.append((start, end))
print(merged)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

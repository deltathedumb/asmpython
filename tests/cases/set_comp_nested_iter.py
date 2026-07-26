# expect:
# [1, 2, 3]
matrix = [[1, 2], [2, 3], [3, 1]]
print(sorted({x for row in matrix for x in row}))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

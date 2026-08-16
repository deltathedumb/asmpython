# expect:
# rows: [6, 15, 24] cols: [12, 15, 18]
grid = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
row_sums = [sum(row) for row in grid]
col_sums = [sum(grid[r][c] for r in range(3)) for c in range(3)]
print('rows:', row_sums, 'cols:', col_sums)
# asmpython (beta/3.14.0) MISMATCH: prints 'rows: [6, 15, 24] cols: [1, 2, 3]\n' (wrong).

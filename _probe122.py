# probe122: list comprehension with walrus producing tuples
data = [1, 4, 9, 16, 25, 36]

# walrus + tuple in comprehension
results = [(x, y) for x in data if (y := x // 4) > 0]
print(len(results))      # 5 (all except 1)
print(results[0])        # (4, 1)
print(results[1])        # (9, 2)
print(results[4])        # (36, 9)

# Multi-level comprehension producing tuples
matrix = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
big = [(i, j, row[j]) for i, row in enumerate(matrix)
       for j in range(len(row))
       if row[j] > 5]
print(len(big))     # 4 (6, 7, 8, 9)
print(big[0])       # (1, 2, 6)
print(big[3])       # (2, 2, 9)

# Comprehension returning list of tuples with computed values
def dist_sq(x: int, y: int) -> int:
    return x * x + y * y

points = [(x, y) for x in range(-2, 3) for y in range(-2, 3)]
close = [(x, y) for x, y in points if dist_sq(x, y) <= 4]
print(len(close))   # 13

# expect:
# 5
# 0
# 6
# 1
# 2
# 3
# 9
# 0
# 0

xs = [0] * 5
print(len(xs))
print(xs[4])

ys = [1, 2] * 3
print(len(ys))
print(ys[0])
print(ys[3])

# list * int in comprehension context
n = 3
matrix = [[0] * n for _ in range(n)]
matrix[1][1] = 9
matrix[0][2] = 0
print(len(matrix))
print(matrix[1][1])
print(matrix[0][2])
print(matrix[2][0])

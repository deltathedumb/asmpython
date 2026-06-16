# probe61: list/dict slicing and advanced indexing
xs = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]

# Negative indexing
print(xs[-1])    # 9
print(xs[-3])    # 7

# Step slicing
print(xs[::2])   # [0, 2, 4, 6, 8]
print(xs[1::2])  # [1, 3, 5, 7, 9]
print(xs[::-1])  # [9, 8, 7, 6, 5, 4, 3, 2, 1, 0] (reversed)

# Slice assignment
xs[2:5] = [20, 30, 40]
print(xs)  # [0, 1, 20, 30, 40, 5, 6, 7, 8, 9]

# Multi-dimensional list (list of lists)
matrix = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
print(matrix[1][2])   # 6
print(matrix[0][0])   # 1

# Unpacking in loop
pairs = [(1, 2), (3, 4), (5, 6)]
for a, b in pairs:
    print(a + b, end=" ")
print()  # 3 7 11

# List as stack
stack = []
stack.append(1)
stack.append(2)
stack.append(3)
print(stack.pop())  # 3
print(stack.pop())  # 2
print(len(stack))   # 1

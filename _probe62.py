# probe62: generators and advanced comprehension patterns
# Multiple for clauses
flat = [(x, y) for x in range(3) for y in range(3)]
print(len(flat))  # 9
print(flat[0])    # (0, 0)
print(flat[4])    # (1, 1)

# Complex condition
pairs = [(x, y) for x in range(5) for y in range(5) if x != y and x + y < 6]
print(len(pairs))  # some count

# Comprehension with function call
def square(n: int) -> int:
    return n * n

squares = [square(x) for x in range(6)]
print(squares)  # [0, 1, 4, 9, 16, 25]

# Nested comprehension (list of lists)
matrix = [[i * j for j in range(1, 4)] for i in range(1, 4)]
print(matrix[0])  # [1, 2, 3]
print(matrix[1])  # [2, 4, 6]
print(matrix[2])  # [3, 6, 9]

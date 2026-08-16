# expect:
# 4
matrix = {(i, j): i * j for i in range(3) for j in range(3)}
print(matrix[(2, 2)])
# asmpython (beta/3.14.0) rejects at compile: [P002] expected OP '}', got KEYWORD 'for'

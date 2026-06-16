# probe83: complex list ops - flatten, transpose, matrix mult
def flatten(lst: list) -> list:
    result = []
    for item in lst:
        for x in item:
            result.append(x)
    return result

matrix = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
flat = flatten(matrix)
print(len(flat))   # 9
print(flat[0])     # 1
print(flat[8])     # 9

# Transpose
def transpose(m: list) -> list:
    rows: int = len(m)
    cols: int = len(m[0])
    result = [[0] * rows for _ in range(cols)]
    for i in range(rows):
        for j in range(cols):
            result[j][i] = m[i][j]
    return result

t = transpose([[1, 2, 3], [4, 5, 6]])
print(len(t))      # 3 (was 2 rows, 3 cols -> 3 rows, 2 cols)
print(t[0][0])     # 1
print(t[0][1])     # 4
print(t[1][0])     # 2
print(t[2][1])     # 6

# List flattening with comprehension
nested = [[1, 2], [3, 4], [5, 6]]
flat2 = [x for sub in nested for x in sub]
print(flat2[0])   # 1
print(flat2[5])   # 6
print(len(flat2)) # 6

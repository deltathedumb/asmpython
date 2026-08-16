# expect:
# [[19, 22], [43, 50]]
def matmul(a, b):
    rows_a = len(a)
    cols_b = len(b[0])
    cols_a = len(a[0])
    result = [[0] * cols_b for _ in range(rows_a)]
    for i in range(rows_a):
        for j in range(cols_b):
            for k in range(cols_a):
                result[i][j] += a[i][k] * b[k][j]
    return result
print(matmul([[1, 2], [3, 4]], [[5, 6], [7, 8]]))
# asmpython (beta/3.14.0) MISMATCH: prints '[9869376, 9868960]\n' (wrong).

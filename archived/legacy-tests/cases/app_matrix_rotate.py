# expect:
# [[3, 1], [4, 2]]
def rotate90(m):
    n = len(m)
    return [[m[n - 1 - j][i] for j in range(n)] for i in range(n)]
result = rotate90([[1, 2], [3, 4]])
print(result)
# asmpython (beta/3.14.0) MISMATCH: prints '[8623344, 8624192]\n' (wrong).

# expect:
# [[[0, 1], [1, 2]], [[1, 2], [2, 3]]]
print([[[i + j + k for k in range(2)] for j in range(2)] for i in range(2)])
# asmpython (beta/3.14.0) MISMATCH: prints '[[9606320, 9606448], [9606688, 9607136]]\n' (wrong).

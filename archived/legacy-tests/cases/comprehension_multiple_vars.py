# expect:
# [(0, 0), (0, 1), (1, 0), (1, 1)]
print([(i, j) for i in range(2) for j in range(2)])
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

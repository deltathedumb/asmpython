# expect:
# [2.0, 2.0]
points = [(0, 0), (1, 2), (2, 4)]
gradients = [(points[i + 1][1] - points[i][1]) / (points[i + 1][0] - points[i][0]) for i in range(len(points) - 1)]
print(gradients)
# asmpython (beta/3.14.0) MISMATCH: prints '[9081792, 9081856]\n' (wrong).

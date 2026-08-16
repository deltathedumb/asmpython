# expect:
# 1 2 3
# 4 5 6
data = [(1, (2, 3)), (4, (5, 6))]
for a, (b, c) in data:
    print(a, b, c)
# asmpython (beta/3.14.0) MISMATCH: prints '1 8885232 0\n4 8885360 0\n' (wrong).

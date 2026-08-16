# expect:
# 1
# 2
# 3
# 4

rows: list[list[int]] = [[1, 2], [3, 4]]
for row in rows:
    for x in row:
        print(x)

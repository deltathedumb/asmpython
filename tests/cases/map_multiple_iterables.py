# expect:
# [6]
print(list(map(lambda x, y, z: x + y + z, [1], [2], [3])))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

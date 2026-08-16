# expect:
# hello
# world
# 3

matrix: list[list[str]] = [["hello", "world"], ["a", "b", "c"]]

row0: list[str] = matrix[0]
print(row0[0])
print(row0[1])
print(len(matrix[1]))

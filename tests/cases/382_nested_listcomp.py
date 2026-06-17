# expect:
# 4
# 1 2 3 4
# 2 4
# 4
# hello
# world

matrix: list[list[int]] = [[1, 2], [3, 4]]
flat = [x for row in matrix for x in row]
print(len(flat))
print(flat[0], flat[1], flat[2], flat[3])

# with filter on inner for
evens = [x for row in matrix for x in row if x % 2 == 0]
print(evens[0], evens[1])

# three levels
nested: list[list[list[int]]] = [[[1, 2], [3]], [[4]]]
deep = [x for outer in nested for inner in outer for x in inner]
print(len(deep))

# str elements
lines: list[list[str]] = [["hello"], ["world"]]
words = [w for line in lines for w in line]
print(words[0])
print(words[1])

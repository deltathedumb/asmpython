# expect:
# [[1, 2], [3, 4], [5, 6]]
# 3
# 3
# 7
# 11
# {'a': [1, 2], 'b': [3, 4]}
# 2
# {'x': {'y': 1, 'z': 2}}
# 1
# {'a': 'x', 'b': 'y'}
# {'a': 1.5, 'b': 2.5}
# {'a': 9.5, 'b': 2.5}
# 9.5
# 7.5

grid = [[1, 2], [3, 4], [5, 6]]
print(grid)
print(grid[1][0])
for row in grid:
    total = 0
    for x in row:
        total += x
    print(total)

d = {"a": [1, 2], "b": [3, 4]}
print(d)
print(d["a"][1])

m = {"x": {"y": 1, "z": 2}}
print(m)
print(m["x"]["y"])

s = {"a": "x", "b": "y"}
print(s)

f = {"a": 1.5, "b": 2.5}
print(f)
f["a"] = 9.5
print(f)
print(f.get("a"))
print(f.get("c", 7.5))

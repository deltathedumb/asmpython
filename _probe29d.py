# name collision: outer x, comp uses x
x = 3
xs = [x * 2 for x in [1, 2, 3]]
print(xs[0])  # 2
print(x)      # 3 (outer x unchanged in Python)

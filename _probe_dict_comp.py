# expect:
# {'a': 1, 'b': 4, 'c': 9}
# {0: 0, 1: 1, 2: 4, 3: 9, 4: 16}
# {'x': 2, 'y': 4}

pairs = [("a", 1), ("b", 2), ("c", 3)]
d = {k: v * v for k, v in pairs}
print(d)

squares = {i: i * i for i in range(5)}
print(squares)

src = {"x": 1, "y": 2, "z": 3}
filtered = {k: v * 2 for k, v in src.items() if v < 3}
print(filtered)

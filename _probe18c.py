# simpler: dict comp from zip of two lists
a = ["x", "y"]
b = [1, 2]
d = {k: v for k, v in zip(a, b)}
print(d.get("x"))

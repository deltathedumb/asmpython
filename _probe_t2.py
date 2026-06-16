# zip
a = [1, 2, 3]
b = ["x", "y", "z"]
for x, y in zip(a, b):
    print(x, y)

# zip with different lengths
for x, y in zip([1, 2, 3, 4], ["a", "b"]):
    print(x, y)

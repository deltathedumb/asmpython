# expect:
# (1, 2, 3)
def point(x, y, z):
    return (x, y, z)
coords = [1, 2, 3]
print(point(*coords))
# asmpython (beta/3.14.0) rejects at compile: [E023] *expr argument unpacking requires a tuple with known element types

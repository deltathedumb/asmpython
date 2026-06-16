# probe: list(zip(...)) as callable
xs = [1, 2, 3]
ys = ["a", "b", "c"]
pairs = list(zip(xs, ys))
print(len(pairs))
for i, s in pairs:
    print(i)
    print(s)

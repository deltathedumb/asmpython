# expect:
# [1, 2, 3, 5]
# [2.5, 3.5]
# ['a', 'c']
# {'b': 2}
# [10, 30]

xs = [1, 2, 3, 4, 5]
del xs[-2]
print(xs)

ys = [1.5, 2.5, 3.5]
del ys[0]
print(ys)

zs = ["a", "b", "c"]
del zs[1]
print(zs)

d = {"a": 1, "b": 2}
del d["a"]
print(d)

ws = [10, 20, 30]
del ws[1]
print(ws)

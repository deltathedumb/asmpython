# expect:
# 1 a
# 2 b
# 3 c
# -1 10
# 0 20
# 1 30
# 5 10
# 6 20
# 7 30
# 100 h
# 101 i

# enumerate(iterable, start): the index variable begins at `start` instead
# of 0. `start` may be a literal, a negative number, or any int expression.

xs = ["a", "b", "c"]
for i, x in enumerate(xs, 1):
    print(i, x)

ys = [10, 20, 30]
for i, y in enumerate(ys, -1):
    print(i, y)

start = 5
for i, y in enumerate(ys, start):
    print(i, y)

s = "hi"
for i, ch in enumerate(s, 100):
    print(i, ch)

# expect:
# 3
# 1
# 2
# 3
# 99
# 1

# list(x): shallow copy of a list or tuple into a fresh, independent list.
# Mutating the copy must not touch the original.

xs = [1, 2, 3]
ys = list(xs)
print(len(ys))
for y in ys:
    print(y)
ys[0] = 99
print(ys[0])   # 99
print(xs[0])   # 1 — original untouched

t = (4, 5, 6)
zs = list(t)
# (no extra output; the copy from a tuple just has to compile and run)

# expect:
# 1 a
# 2 b
# 3 c
# 5 a
# 6 b
# 7 c

xs: list[str] = ["a", "b", "c"]
for i, x in enumerate(xs, start=1):
    print(i, x)

for i, x in enumerate(xs, 5):
    print(i, x)

# expect:
# 1
# 2
# 3
# 4
# 5
# 2
# 4
# 6
# 0
# 1
# 2

from itertools import chain, islice, repeat

a: list = [1, 2, 3]
b: list = [4, 5]
chained: list = chain(a, b)
for x in chained:
    print(x)

src: list = [1, 2, 3, 4, 5, 6]
sliced: list = islice(src, 1, 7, 2)
for x in sliced:
    print(x)

rep: list = repeat(0, 3)
i: int = 0
for v in rep:
    print(i)
    i = i + 1

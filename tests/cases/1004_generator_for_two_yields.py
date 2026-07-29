# expect:
# 1
# 101
# 2
# 102
# 3
# 103
def doubled(xs: list[int]) -> list[int]:
    for x in xs:
        yield x
        yield x + 100

for v in doubled([1, 2, 3]):
    print(v)

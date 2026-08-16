# expect:
# -1
# 2
# 0
# 4
def classify(xs: list[int]) -> list[int]:
    for x in xs:
        if x > 0:
            if x % 2 == 0:
                yield x
            else:
                yield -x
        else:
            yield 0

for v in classify([1, 2, -3, 4]):
    print(v)

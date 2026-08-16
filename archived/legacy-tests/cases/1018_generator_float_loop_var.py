# expect:
# 3.0
# 4.5
def scaled(xs: list[float]) -> list[float]:
    for x in xs:
        yield x * 2.0

for v in scaled([1.5, 2.25]):
    print(v)

# expect:
# 1.0
# 0.5
# 0.25
# 0.125
def halves(n: int) -> list[float]:
    x = 1.0
    i = 0
    while i < n:
        yield x
        x = x / 2.0
        i = i + 1

for v in halves(4):
    print(v)

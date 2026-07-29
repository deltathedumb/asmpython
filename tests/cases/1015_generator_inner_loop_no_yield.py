# expect:
# 12
# 22
def filtered(xs: list[int]) -> list[int]:
    for x in xs:
        total = 0
        for k in range(3):
            if k == 1:
                continue
            total = total + k
        yield x + total

for v in filtered([10, 20]):
    print(v)

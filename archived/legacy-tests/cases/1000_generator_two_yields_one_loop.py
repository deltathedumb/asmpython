# expect:
# 0
# 0
# 1
# 10
# 2
# 20
def pairs(n: int) -> list[int]:
    i = 0
    while i < n:
        yield i
        yield i * 10
        i = i + 1

for v in pairs(3):
    print(v)

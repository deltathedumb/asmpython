# expect:
# 1
# 2
# 4
# 5
# 7
# 8
# 10
def upto(n: int) -> list[int]:
    i = 0
    while i < 100:
        i = i + 1
        if i > n:
            break
        if i % 3 == 0:
            continue
        yield i

for v in upto(10):
    print(v)

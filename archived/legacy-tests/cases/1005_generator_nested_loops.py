# expect:
# 0
# 1
# 2
# 10
# 11
# 12
# 20
# 21
# 22
def grid(n: int) -> list[int]:
    i = 0
    while i < n:
        j = 0
        while j < n:
            yield i * 10 + j
            j = j + 1
        i = i + 1

for v in grid(3):
    print(v)

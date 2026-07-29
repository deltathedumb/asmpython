# expect:
# 0
# 1
# 10
# 11
# 20
# 21
def nested(n: int) -> list[int]:
    i = 0
    while i < n:
        for j in range(2):
            yield i * 10 + j
        i = i + 1

for v in nested(3):
    print(v)

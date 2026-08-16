# expect:
# 10
# 11
# 12
def count(start: int) -> list[int]:
    n = start
    i = 0
    while i < 3:
        yield n
        n = n + 1
        i = i + 1

for v in count(10):
    print(v)

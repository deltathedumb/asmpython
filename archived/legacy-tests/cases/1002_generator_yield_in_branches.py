# expect:
# 0
# -1
# 2
# -3
# 4
def evens(n: int) -> list[int]:
    i = 0
    while i < n:
        if i % 2 == 0:
            yield i
        else:
            yield -i
        i = i + 1

for v in evens(5):
    print(v)

# expect:
# 0
# 1
# 2
# 3
def upto(n: int) -> list[int]:
    i = 0
    while True:
        if i >= n:
            return
        yield i
        i = i + 1

for v in upto(4):
    print(v)

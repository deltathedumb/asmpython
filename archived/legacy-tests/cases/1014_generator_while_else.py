# expect:
# 0
# 1
# 2
# 99
def upto(n: int) -> list[int]:
    i = 0
    while i < n:
        yield i
        i = i + 1
    else:
        yield 99

for v in upto(3):
    print(v)

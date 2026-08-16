# expect:
# -1
# 0
# 1
# 2
# -2
def bracket(n: int) -> list[int]:
    yield -1
    i = 0
    while i < n:
        yield i
        i = i + 1
    yield -2

for v in bracket(3):
    print(v)

# expect:
# 5
# 0
# 1
# 4
# 9
# 16
def squares(n: int) -> list[int]:
    i = 0
    while i < n:
        yield i * i
        i = i + 1

xs = list(squares(5))
print(len(xs))
for v in xs:
    print(v)

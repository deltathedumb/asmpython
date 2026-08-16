# expect:
# 0
# 0
# 1
# 1
# 2
def count(n: int) -> list[int]:
    i = 0
    while i < n:
        yield i
        i = i + 1

a = count(3)
b = count(2)
print(next(a))
print(next(b))
print(next(a))
print(next(b))
print(next(a))

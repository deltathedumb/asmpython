# probe88: generator functions with yield
def count_up(start: int, end: int):
    n: int = start
    while n <= end:
        yield n
        n = n + 1

gen = count_up(1, 5)
for v in gen:
    print(v, end=" ")
print()   # 1 2 3 4 5

# Generator with list
vals = list(count_up(10, 15))
print(len(vals))   # 6
print(vals[0])     # 10
print(vals[5])     # 15

# yield in class
class Range:
    def __init__(self, n: int) -> None:
        self.n = n

    def __iter__(self):
        i: int = 0
        while i < self.n:
            yield i
            i = i + 1

r = Range(5)
for v in r:
    print(v, end=" ")
print()   # 0 1 2 3 4

# probe128: complex generator + iterator protocol patterns
def take(n: int, iterable):
    count: int = 0
    for item in iterable:
        if count >= n:
            return
        yield item
        count += 1

def naturals():
    n: int = 1
    while True:
        yield n
        n += 1

# Take first 5 natural numbers
for v in take(5, naturals()):
    print(v, end=" ")
print()   # 1 2 3 4 5

# Generator pipeline
def squares_gen():
    n: int = 1
    while True:
        yield n * n
        n += 1

first_10_squares = list(take(10, squares_gen()))
print(first_10_squares)   # [1, 4, 9, 16, 25, 36, 49, 64, 81, 100]

# Generator with send-like pattern (accumulator)
def running_sum():
    total: int = 0
    for v in range(10):
        total += v
        yield total

rs = list(running_sum())
print(rs)   # [0, 1, 3, 6, 10, 15, 21, 28, 36, 45]
print(rs[4])  # 10
print(rs[9])  # 45

# expect:
# 1
# 2
# 3
# 10
# 20
# 30

def make_counter():
    count = 0
    def increment():
        nonlocal count
        count += 1
        return count
    return increment

c = make_counter()
print(c())
print(c())
print(c())

def make_accumulator(start: int):
    total = start
    def add(n: int) -> int:
        nonlocal total
        total += n
        return total
    return add

acc = make_accumulator(0)
print(acc(10))
print(acc(10))
print(acc(10))

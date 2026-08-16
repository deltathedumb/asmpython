# expect:
# 0
# 1
# 2
# 3
# 4
# [0, 2, 4, 6, 8]
# [1, 4, 9, 16]

def count_up(n: int):
    i = 0
    while i < n:
        yield i
        i += 1

for x in count_up(5):
    print(x)

def even_multiples(n: int, factor: int):
    i = 0
    while i < n:
        yield i * factor
        i += 2

evens = [v for v in even_multiples(10, 1)]
print(evens)

squares = [x*x for x in count_up(5) if x > 0]
print(squares)

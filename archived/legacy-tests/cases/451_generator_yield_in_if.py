# expect:
# [0, 2, 4]
# [0, 3, 6, 9]
# [0, 100, 20, 300]

def gen_even(n: int):
    for i in range(n):
        if i % 2 == 0:
            yield i

def gen_filter(n: int):
    i: int = 0
    while i < n:
        if i % 3 == 0:
            yield i
        i += 1

def gen_two_branch(n: int):
    for i in range(n):
        if i % 2 == 0:
            yield i * 10
        else:
            yield i * 100

evens: list = [v for v in gen_even(6)]
print(evens)

mults: list = [v for v in gen_filter(10)]
print(mults)

branches: list = [v for v in gen_two_branch(4)]
print(branches)

def gen_range(n: int):
    i = 0
    while i < n:
        yield i
        i += 1

for x in gen_range(5):
    print(x)

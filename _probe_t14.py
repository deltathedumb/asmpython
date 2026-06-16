# Generator functions
def gen_range(n: int):
    i = 0
    while i < n:
        yield i
        i += 1

for x in gen_range(5):
    print(x)

# Generator expressions
squares = (x*x for x in range(1, 6))
for s in squares:
    print(s)

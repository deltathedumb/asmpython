# probes: __iter__ implemented as a generator
# expect:
# [0, 1, 1, 2, 3]
class Fib:
    def __iter__(self):
        a, b = 0, 1
        for _ in range(5):
            yield a
            a, b = b, a + b


print(list(Fib()))

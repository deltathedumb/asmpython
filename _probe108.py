# probe108: iterator protocol, custom __iter__
class Fibonacci:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.a = 0
        self.b = 1

    def __iter__(self) -> "Fibonacci":
        self.a = 0
        self.b = 1
        return self

    def __next__(self) -> int:
        if self.a > self.limit:
            raise StopIteration()
        val: int = self.a
        self.a, self.b = self.b, self.a + self.b
        return val

fib = Fibonacci(100)
for v in fib:
    print(v, end=" ")
print()   # 0 1 1 2 3 5 8 13 21 34 55 89

# Can iterate multiple times
total: int = 0
for v in fib:
    total = total + v
print(total)   # 273

# Convert to list
vals = list(Fibonacci(20))
print(vals)   # [0, 1, 1, 2, 3, 5, 8, 13]
print(len(vals))  # 8

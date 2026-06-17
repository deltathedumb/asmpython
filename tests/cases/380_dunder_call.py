# expect:
# 8
# 15
# 18

class Adder:
    def __init__(self, n: int) -> None:
        self.n: int = n

    def __call__(self, x: int) -> int:
        return self.n + x

class Multiplier:
    def __init__(self, factor: int) -> None:
        self.factor: int = factor

    def __call__(self, a: int, b: int) -> int:
        return (a + b) * self.factor

add5 = Adder(5)
print(add5(3))
print(add5(10))
m = Multiplier(3)
print(m(2, 4))

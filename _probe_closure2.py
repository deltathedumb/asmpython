def make_adder(n: int):
    def adder(x: int) -> int:
        return x + n
    return adder

add5 = make_adder(5)
print(add5(10))

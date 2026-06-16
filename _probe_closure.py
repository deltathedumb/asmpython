# expect:
# 15
# 5
# adder: 10

def make_adder(n: int):
    def adder(x: int) -> int:
        return x + n
    return adder

add5 = make_adder(5)
print(add5(10))
print(add5(0))

def apply(f, x: int) -> int:
    return f(x)

result: int = apply(make_adder(3), 7)
print("adder: " + str(result))

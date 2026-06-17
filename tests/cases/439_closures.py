# expect:
# 15
# 13
# 14
# 12
# 11

def make_adder(n: int):
    def adder(x: int) -> int:
        return x + n
    return adder

def make_multiplier(factor: int):
    def mul(x: int) -> int:
        return x * factor
    return mul

add5 = make_adder(5)
add10 = make_adder(10)
double = make_multiplier(2)
triple = make_multiplier(3)

print(add5(10))
print(add10(3))
print(double(7))
print(triple(4))
print(add5(add5(1)))

# probes: a returned closure keeps its captured value
# expect:
# 6
# 11
def make_adder(n):
    def add(v):
        return v + n

    return add


add5 = make_adder(5)
add10 = make_adder(10)
print(add5(1))
print(add10(1))

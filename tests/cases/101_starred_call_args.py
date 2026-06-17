# expect:
# 6
# Alice is 30
# 1
# 2

def add3(a: int, b: int, c: int) -> int:
    return a + b + c

t = (1, 2, 3)
print(add3(*t))

def describe(name: str, age: int) -> None:
    print(name + " is " + str(age))

person = ("Alice", 30)
describe(*person)

pairs = [(1, "one"), (2, "two")]

def first(n: int, label: str) -> int:
    return n

print(first(*pairs[0]))
print(first(*pairs[1]))

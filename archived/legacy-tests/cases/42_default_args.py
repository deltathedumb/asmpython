# expect:
# 11
# 20
# 7
# hello
# hi
# bye
# 6
# 25
# 10
def add(x, y=10):
    return x + y

print(add(1))
print(add(10, 10))

def offset(value, by=-3):
    return value + by

print(offset(10))

def greet(prefix="hello"):
    print(prefix)

greet()
greet("hi")
greet("bye")

def triple(a, b=2, c=3):
    return a + b + c

print(triple(1))
print(triple(20))
print(triple(5, 2, 3))

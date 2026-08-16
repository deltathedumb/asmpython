# expect:
# 8.0
# 1024.0
# 3.0
# 2.0
# 6.25
# 0.1
# 1024
# 8.0
# 9.0

print(2.0 ** 3)
print(2.0 ** 10)
print(9.0 ** 0.5)
print(4.0 ** 0.5)
print(2.5 ** 2)
print(10.0 ** -1)
print(2 ** 10)

a = 2.0
a **= 3.0
print(a)


def square(x: float) -> float:
    return x ** 2.0


print(square(3.0))

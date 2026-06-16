# probe115: deeply nested function calls, complex expressions
def f(x: int) -> int:
    return x + 1

def g(x: int) -> int:
    return x * 2

def h(x: int) -> int:
    return x - 3

# Deeply nested calls
print(f(g(h(10))))     # h(10)=7, g(7)=14, f(14)=15
print(g(f(g(f(1)))))  # f(1)=2, g(2)=4, f(4)=5, g(5)=10

# Complex arithmetic
a, b, c = 3, 4, 5
print(a * b + c)           # 17
print(a * (b + c))         # 27
print((a + b) * c)         # 35
print(a ** 2 + b ** 2)     # 25
print((a ** 2 + b ** 2) == c ** 2)  # True

# Chained comparisons
x = 5
print(1 < x < 10)    # True
print(1 < x < 4)     # False
print(x == 5 == 5)   # True
print(1 <= x <= 5)   # True
print(1 <= x < 5)    # False

# Mixed expressions
nums = [1, 2, 3, 4, 5]
print(sum(nums) * len(nums))   # 15 * 5 = 75
print(max(nums) - min(nums))   # 4
print(sorted(nums, reverse=True)[0])  # 5

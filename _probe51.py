# probe51: generators and next()
# Generator expressions passed to sum/any/all
nums = [1, 2, 3, 4, 5]
total = sum(x * x for x in nums)
print(total)   # 55

# Generator in any/all
print(any(x > 3 for x in nums))  # True
print(all(x > 0 for x in nums))  # True

# zip iteration
a = [1, 2, 3]
b = [4, 5, 6]
total2 = 0
for x, y in zip(a, b):
    total2 += x + y
print(total2)  # 21

# Nested function calls
def apply(f: int, n: int) -> int:
    return f + n

result = apply(10, apply(5, 3))
print(result)  # 18

# *args (varargs)
def sumall(*args: int) -> int:
    total = 0
    for x in args:
        total += x
    return total

print(sumall(1, 2, 3, 4, 5))  # 15

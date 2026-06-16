# probe69: generator expressions, itertools-style patterns
# Generator in sum/list/any/all
nums = [1, 2, 3, 4, 5]
total = sum(x * x for x in nums)
print(total)   # 55

sq = list(x * x for x in nums)
print(sq[0])   # 1
print(sq[4])   # 25

b1 = any(x > 4 for x in nums)
b2 = all(x > 0 for x in nums)
print(b1)   # True
print(b2)   # True

b3 = all(x > 1 for x in nums)
print(b3)   # False

# Chained operations
evens = [x for x in range(10) if x % 2 == 0]
doubled = [x * 2 for x in evens]
print(doubled)   # [0, 4, 8, 12, 16]

# Conditional expression in comprehension
result = ["even" if x % 2 == 0 else "odd" for x in range(5)]
print(result[0])   # even
print(result[1])   # odd
print(result[4])   # even

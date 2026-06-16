# probe44: advanced control flow and builtins
# walrus operator :=
data = [1, 2, 3, 4, 5]
if (n := len(data)) > 3:
    print(n)  # 5

# while with walrus
items = [10, 20, 30]
idx = 0
while (val := items[idx]) < 25:
    print(val)
    idx += 1
# 10
# 20

# nested list comprehension
matrix = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
flat = [x for row in matrix for x in row]
print(flat)  # [1, 2, 3, 4, 5, 6, 7, 8, 9]

# list comp with if filter
evens = [x for x in range(10) if x % 2 == 0]
print(evens)  # [0, 2, 4, 6, 8]

# set comprehension
nums = {x * x for x in range(5)}
# sets are unordered, so just check membership
print(4 in nums)   # True
print(9 in nums)   # True
print(5 in nums)   # False

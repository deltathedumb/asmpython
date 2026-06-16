# probe114: walrus operator in comprehensions
# walrus in list comprehension filter
nums = [1, 4, 9, 16, 25, 36]
results = [y for x in nums if (y := x - 1) > 5]
print(results)   # [8, 15, 24, 35]

# walrus to compute once per element
import math
data = [1, 4, 9, 16, 25]
roots = [r for x in data if (r := math.sqrt(x)) > 2.0]
print(len(roots))   # 3  (sqrt(9)=3, sqrt(16)=4, sqrt(25)=5)
print(roots[0])     # 3.0
print(roots[2])     # 5.0

# walrus in while to process list
items = [1, 2, 3, 4, 5]
total: int = 0
idx: int = 0
while (idx < len(items)) and (v := items[idx]):
    total = total + v
    idx = idx + 1
print(total)   # 15

# walrus for running max
xs = [3, 1, 4, 1, 5, 9, 2, 6]
mx: int = 0
highs = [mx := x for x in xs if x > mx]
print(highs)    # [3, 4, 5, 9]
print(mx)       # 9

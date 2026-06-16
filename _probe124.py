# probe124: complex assignment patterns - starred in various positions
# Starred in middle
a, *b, c = [1, 2, 3, 4, 5]
print(a)        # 1
print(b)        # [2, 3, 4]
print(c)        # 5

# Starred at start
*x, last = range(5)
print(x)        # [0, 1, 2, 3]
print(last)     # 4

# Nested tuple destructuring
(a, b), c = (1, 2), 3
print(a, b, c)   # 1 2 3

# Destructuring in for loop with complex pattern
data = [(1, [2, 3]), (4, [5, 6]), (7, [8, 9])]
for head, tail in data:
    print(head, tail[0], tail[1])
# 1 2 3
# 4 5 6
# 7 8 9

# Multiple assignment with complex right side
def get_pair() -> tuple:
    return (10, 20)

x, y = get_pair()
print(x, y)   # 10 20

# Swap using tuple
a, b = 1, 2
a, b = b, a + b
print(a, b)   # 2 3

# probe: more patterns

# 1. dict comprehension
xs = [1, 2, 3, 4, 5]
squares = {str(x): x * x for x in xs}
print(squares["3"])
print(squares["5"])

# 2. set comprehension
evens = {x for x in xs if x % 2 == 0}
print(2 in evens)
print(3 in evens)

# 3. nested list comprehension
matrix = [[1, 2, 3], [4, 5, 6], [7, 8, 9]]
flat = [x for row in matrix for x in row]
print(len(flat))
print(flat[4])

# 4. conditional expression (ternary)
x = 10
label = "big" if x > 5 else "small"
print(label)

# probe99: complex lambda patterns, partial application
# Lambda with multiple args
add = lambda x, y: x + y
mul = lambda x, y: x * y
print(add(3, 4))    # 7
print(mul(3, 4))    # 12

# Lambda in sorted
data = [(3, "c"), (1, "a"), (2, "b")]
data.sort(key=lambda p: p[0])
print(data[0][1])   # a
print(data[1][1])   # b
print(data[2][1])   # c

# Lambda in map/filter
nums = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
evens = list(filter(lambda x: x % 2 == 0, nums))
doubled = list(map(lambda x: x * 2, evens))
print(doubled)   # [4, 8, 12, 16, 20]

# Nested lambda
apply = lambda f, x: f(x)
print(apply(lambda x: x * x, 5))   # 25
print(apply(lambda x: x + 1, 10))  # 11

# Lambda capturing closure
def make_multiplier(n: int):
    return lambda x: x * n

triple = make_multiplier(3)
quadruple = make_multiplier(4)
print(triple(5))      # 15
print(quadruple(5))   # 20
print(triple(10))     # 30

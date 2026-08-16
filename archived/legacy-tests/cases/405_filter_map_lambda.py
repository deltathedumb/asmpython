# expect:
# 2
# 2
# 4
# 1
# 25

xs = [1, 2, 3, 4, 5]
evens = list(filter(lambda x: x % 2 == 0, xs))
print(len(evens))
print(evens[0])
print(evens[1])
squares = list(map(lambda x: x * x, xs))
print(squares[0])
print(squares[4])

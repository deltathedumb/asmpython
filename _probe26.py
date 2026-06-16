# probe26: generator expressions, map, filter (via list())
# map with lambda
nums = [1, 2, 3, 4, 5]
doubled = list(map(lambda x: x * 2, nums))
print(doubled[0])    # 2
print(doubled[4])    # 10

# filter with lambda
evens = list(filter(lambda x: x % 2 == 0, nums))
print(len(evens))    # 2
print(evens[0])      # 2
print(evens[1])      # 4

# generator expression via list()
squares = list(x * x for x in range(5))
print(squares[0])    # 0
print(squares[4])    # 16

# nested list comprehension
matrix = [[i * j for j in range(1, 4)] for i in range(1, 4)]
print(matrix[0][0])  # 1
print(matrix[1][1])  # 4
print(matrix[2][2])  # 9

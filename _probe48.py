# probe48: lambda, closures, higher-order functions
# lambda as key
nums = [3, 1, 4, 1, 5, 9, 2, 6]
s = sorted(nums, key=lambda x: -x)
print(s)  # [9, 6, 5, 4, 3, 2, 1, 1]

# map with lambda
doubled = list(map(lambda x: x * 2, [1, 2, 3, 4]))
print(doubled)  # [2, 4, 6, 8]

# filter with lambda
evens = list(filter(lambda x: x % 2 == 0, range(10)))
print(evens)  # [0, 2, 4, 6, 8]

# lambda in list
ops = [lambda x: x + 1, lambda x: x * 2, lambda x: x * x]
for op in ops:
    print(op(3))  # 4, 6, 9

# sorted with key on strings
words = ["banana", "apple", "cherry", "date"]
print(sorted(words, key=lambda w: len(w)))  # ['date', 'apple', 'banana', 'cherry']

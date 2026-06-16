# probe64: enumerate, zip, map, filter, any, all
nums = [1, 2, 3, 4, 5]

# enumerate
for i, v in enumerate(nums):
    if i == 0:
        print(v)   # 1
    if i == 4:
        print(v)   # 5

# enumerate with start
for i, v in enumerate(nums, 10):
    if i == 10:
        print(v)   # 1
    if i == 12:
        print(v)   # 3

# zip
a = [1, 2, 3]
b = ["x", "y", "z"]
pairs = list(zip(a, b))
print(len(pairs))   # 3
# zip iteration
for n, c in zip(a, b):
    print(n, c, end=" ")
print()

# any / all
print(any([False, True, False]))   # True
print(any([False, False]))          # False
print(all([True, True, True]))     # True
print(all([True, False, True]))    # False

# map
doubled = list(map(lambda x: x * 2, nums))
print(doubled[0])   # 2
print(doubled[4])   # 10

# filter
evens = list(filter(lambda x: x % 2 == 0, nums))
print(len(evens))   # 2
print(evens[0])     # 2
print(evens[1])     # 4

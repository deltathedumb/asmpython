# expect:
# [1, 4, 7, 10]
nums = range(10)
result = list(map(lambda x: x + 1, filter(lambda x: x % 3 == 0, nums)))
print(result)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

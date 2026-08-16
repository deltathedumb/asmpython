# expect:
# [1, 2, 3]
data = [1, 2, 3, 0, 4]
it = iter(data)
result = []
for x in iter(lambda: next(it), 0):
    result.append(x)
print(result)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

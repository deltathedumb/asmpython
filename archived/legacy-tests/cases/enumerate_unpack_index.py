# expect:
# [(0, 'a'), (1, 'b'), (2, 'c')]
items = ['a', 'b', 'c']
print([(i, v) for i, v in enumerate(items)])
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

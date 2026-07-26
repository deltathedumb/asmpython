# expect:
# [(1, 'a'), (1, 'c'), (2, 'b')]
data = [(1, 'a'), (2, 'b'), (1, 'c')]
print(sorted(data, key=lambda x: x[0]))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

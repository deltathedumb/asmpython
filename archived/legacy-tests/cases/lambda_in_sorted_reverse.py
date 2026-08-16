# expect:
# [(2, 'a'), (1, 'z')]
pairs = [(1, 'z'), (2, 'a')]
print(sorted(pairs, key=lambda p: p[1]))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

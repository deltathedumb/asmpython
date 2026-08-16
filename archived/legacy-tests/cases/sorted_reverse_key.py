# expect:
# [(1, 'b'), (2, 'a')]
print(sorted([(1, 'b'), (2, 'a')], key=lambda p: p[1], reverse=True))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005

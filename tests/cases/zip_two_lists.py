# expect:
# [(1, 3), (2, 4)]
print(list(zip([1, 2], [3, 4])))
# asmpython (beta/3.14.0): runtime segfault (exit 0xC0000005) materializing
# list(zip(...)).

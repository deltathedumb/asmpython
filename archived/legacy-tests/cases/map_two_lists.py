# expect:
# [4, 6]
print(list(map(lambda a, b: a + b, [1, 2], [3, 4])))
# map() over two lists segfaults at runtime (0xC0000005).

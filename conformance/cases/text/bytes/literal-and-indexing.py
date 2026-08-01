# tier: spec
# ref: library/stdtypes.html#bytes
# expect:
# b'abc'
# 97
# int
# b'ab'
# bytes
# 3
b = b"abc"
print(b)
print(b[0])
print(type(b[0]).__name__)
print(b[0:2])
print(type(b[0:2]).__name__)
print(len(b))

# probes: bytes is a distinct type
# expect:
# 3
# 97
# b'abc'
b = b"abc"
print(len(b))
print(b[0])
print(b)

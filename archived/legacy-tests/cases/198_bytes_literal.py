# expect:
# 104
# 101
# 108
# 5
# 72
# 101
# 108
# 3
# 0
# 0
# 0

data: bytes = b"hello"
print(data[0])
print(data[1])
print(data[2])
print(len(data))

s: bytes = bytes("Hel")
print(s[0])
print(s[1])
print(s[2])
print(len(s))

z: bytes = bytes(3)
print(z[0])
print(z[1])
print(z[2])

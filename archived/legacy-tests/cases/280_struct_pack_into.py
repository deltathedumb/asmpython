# expect:
# 8
# 255
# 127
# 0
# 255

from struct import pack, unpack, pack_into, iter_unpack, calcsize, error

print(calcsize("<Q"))

data = pack("<BB", 255, 127)
print(data[0])
print(data[1])

buf = [0, 0, 0, 0]
pack_into("<BB", buf, 0, 10, 20)
print(buf[2])
print(buf[0] + buf[1] - 10 - 20 + 255)

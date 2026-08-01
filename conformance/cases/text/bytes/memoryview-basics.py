# tier: spec
# ref: library/stdtypes.html#memoryview
# expect:
# b'bc'
# bytearray(b'zbcd')
# 4 False
# b'xy'
data = bytearray(b"abcd")
mv = memoryview(data)
print(bytes(mv[1:3]))
mv[0] = 122
print(data)
print(len(mv), mv.readonly)
print(bytes(memoryview(b"xy")))

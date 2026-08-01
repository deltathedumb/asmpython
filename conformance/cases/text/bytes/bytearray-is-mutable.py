# tier: spec
# ref: library/stdtypes.html#bytearray
# expect:
# bytearray(b'zbc')
# b'zbc'
# TypeError
ba = bytearray(b"abc")
ba[0] = 122
print(ba)
print(bytes(ba))
try:
    b"abc"[0] = 122
except TypeError:
    print("TypeError")

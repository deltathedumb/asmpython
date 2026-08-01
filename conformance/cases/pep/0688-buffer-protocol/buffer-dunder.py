# tier: spec
# ref: reference/datamodel.html#emulating-buffer-types
# min-python: 3.12
# expect:
# b'abcd'
# 4 1 B
# b'yx'
# True
data = bytearray(b"abcd")
mv = memoryview(data)
print(bytes(mv))
print(mv.nbytes, mv.itemsize, mv.format)
print(bytes(memoryview(b"xy")[::-1]))
print(hasattr(bytes, "__buffer__"))

# tier: spec
# ref: library/stdtypes.html#printf-style-bytes-formatting
# expect:
# b'3'
# b'ab'
# b'ff'
# bytes
print(b"%d" % 3)
print(b"%s" % b"ab")
print(b"%x" % 255)
print(type(b"%d" % 3).__name__)

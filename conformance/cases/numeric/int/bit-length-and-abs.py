# tier: spec
# ref: library/stdtypes.html#int.bit_length
# expect:
# 0 0 0
# 1 1 1
# 255 8 255
# 256 9 256
# -255 8 255
for v in (0, 1, 255, 256, -255):
    print(v, v.bit_length(), abs(v))

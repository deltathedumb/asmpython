# tier: spec
# ref: reference/datamodel.html#object.__index__
# expect:
# 30
# cd
# 0x2 0o2 0b10
# 3
# [0, 1]
class Two:
    def __index__(self):
        return 2

print([10, 20, 30][Two()])
print("abcd"[Two():])
print(hex(Two()), oct(Two()), bin(Two()))
print((1, 2, 3)[Two()])
print(list(range(Two())))

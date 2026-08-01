# tier: spec
# ref: reference/datamodel.html#slots
# expect:
# ValueError
# 1
# ('v',)
# member_descriptor
try:
    class Bad:
        __slots__ = ("v",)
        v = 1
except ValueError:
    print("ValueError")

class Ok:
    __slots__ = ("v",)

o = Ok()
o.v = 1
print(o.v)
print(Ok.__slots__)
print(type(Ok.v).__name__)

# tier: spec
# ref: reference/datamodel.html#slots
# expect:
# 3
# AttributeError
class P:
    __slots__ = ("x",)
    def __init__(self, x):
        self.x = x

p = P(3)
print(p.x)
try:
    p.y = 4
except AttributeError as e:
    print("AttributeError")

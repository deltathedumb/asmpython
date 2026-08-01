# tier: spec
# ref: reference/datamodel.html#object.__ne__
# expect:
# True
# False
# True
class OnlyEq:
    def __init__(self, v):
        self.v = v
    def __eq__(self, o):
        return self.v == o.v

print(OnlyEq(1) == OnlyEq(1))
print(OnlyEq(1) != OnlyEq(1))
print(OnlyEq(1) != OnlyEq(2))

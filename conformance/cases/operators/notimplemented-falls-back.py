# tier: spec
# ref: reference/datamodel.html#object.__eq__
# expect:
# right-wins
# False
class Left:
    def __eq__(self, other):
        return NotImplemented

class Right:
    def __eq__(self, other):
        return "right-wins"

print(Left() == Right())
print(Left() == Left())

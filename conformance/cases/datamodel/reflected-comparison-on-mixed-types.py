# tier: spec
# ref: reference/datamodel.html#object.__lt__
# expect:
# left-lt
# right-gt
# True False
class Left:
    def __lt__(self, o):
        return "left-lt"

class Right:
    def __gt__(self, o):
        return "right-gt"

print(Left() < Right())
print(Right() > Left())
print(1 < 2.0, 2.0 < 1)

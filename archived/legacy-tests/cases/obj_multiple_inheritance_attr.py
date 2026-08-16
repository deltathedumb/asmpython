# probes: attribute lookup follows the MRO left to right
# expect:
# left
# only-right
class Left:
    kind = "left"


class Right:
    kind = "right"
    other = "only-right"


class Both(Left, Right):
    pass


print(Both.kind)
print(Both.other)

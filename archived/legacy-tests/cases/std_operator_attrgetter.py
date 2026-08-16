# probes: attrgetter reads a named attribute
# expect:
# 9
import operator


class Holder:
    def __init__(self, v):
        self.v = v


print(operator.attrgetter("v")(Holder(9)))

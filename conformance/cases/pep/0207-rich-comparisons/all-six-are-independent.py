# tier: spec
# ref: reference/datamodel.html#object.__lt__
# expect:
# lt gt eq
# lt gt
# True
class Odd:
    def __lt__(self, o): return "lt"
    def __gt__(self, o): return "gt"
    def __eq__(self, o): return "eq"

o = Odd()
print(o < 1, o > 1, o == 1)
print(1 > o, 1 < o)
print(bool([] < [1]))

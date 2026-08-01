# tier: spec
# ref: reference/datamodel.html#object.__contains__
# expect:
# True False
# True
class Odd:
    def __contains__(self, v):
        return v % 2 == 1

o = Odd()
print(1 in o, 2 in o)
print(2 not in o)

# tier: spec
# ref: reference/datamodel.html#object.__iter__
# expect:
# TypeError
# [1, 2]
class BadIter:
    def __iter__(self):
        return "not-an-iterator"

try:
    list(BadIter())
except TypeError:
    print("TypeError")

class GoodIter:
    def __iter__(self):
        return iter([1, 2])

print(list(GoodIter()))

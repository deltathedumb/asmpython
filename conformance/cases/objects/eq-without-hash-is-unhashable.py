# tier: spec
# ref: reference/datamodel.html#object.__hash__
# expect:
# None
# TypeError
class C:
    def __eq__(self, other):
        return True

print(C.__hash__)
try:
    hash(C())
except TypeError:
    print("TypeError")

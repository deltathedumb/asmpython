# tier: spec
# ref: reference/datamodel.html#custom-classes
# expect:
# DBCA
# ['D', 'B', 'C', 'A', 'object']
class A:
    def who(self):
        return "A"

class B(A):
    def who(self):
        return "B" + super().who()

class C(A):
    def who(self):
        return "C" + super().who()

class D(B, C):
    def who(self):
        return "D" + super().who()

print(D().who())
print([k.__name__ for k in D.__mro__])

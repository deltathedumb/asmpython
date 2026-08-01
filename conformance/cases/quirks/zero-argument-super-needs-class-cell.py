# tier: spec
# ref: library/functions.html#super
# expect:
# B->A
# C->A
class A:
    def who(self):
        return "A"

class B(A):
    def who(self):
        return "B->" + super().who()

class C(A):
    def who(self):
        return "C->" + super(C, self).who()

print(B().who())
print(C().who())

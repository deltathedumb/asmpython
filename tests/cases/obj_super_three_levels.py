# probes: super() walks a three-deep chain
# expect:
# CBA
class A:
    def tag(self):
        return "A"


class B(A):
    def tag(self):
        return "B" + super().tag()


class C(B):
    def tag(self):
        return "C" + super().tag()


print(C().tag())

# tier: spec
# ref: library/functions.html#classmethod
# expect:
# base
# derived
# 6
class C:
    tag = "base"
    @classmethod
    def make(cls):
        return cls.tag
    @staticmethod
    def plain(n):
        return n * 2

class D(C):
    tag = "derived"

print(C.make())
print(D.make())
print(C.plain(3))

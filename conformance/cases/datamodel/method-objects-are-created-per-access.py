# tier: cpython
# ref: reference/datamodel.html#instance-methods
# expect:
# True
# False
# True
# True
class C:
    def m(self):
        return 1

c = C()
print(c.m == c.m)
print(c.m is c.m)
print(C.m is C.m)
print(c.m() == 1)

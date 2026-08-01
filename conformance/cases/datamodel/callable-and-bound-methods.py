# tier: spec
# ref: reference/datamodel.html#instance-methods
# expect:
# bound
# bound
# bound
# True
# True
class C:
    def m(self):
        return "bound"

c = C()
print(c.m())
print(C.m(c))
bm = c.m
print(bm())
print(bm.__self__ is c)
print(bm.__func__ is C.m)

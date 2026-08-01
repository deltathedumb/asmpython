# tier: spec
# ref: reference/datamodel.html#invoking-descriptors
# expect:
# data-descriptor
# instance
# 1
class Data:
    def __get__(self, obj, t=None): return "data-descriptor"
    def __set__(self, obj, v): obj.__dict__["v"] = v

class NonData:
    def __get__(self, obj, t=None): return "non-data-descriptor"

class C:
    v = Data()
    n = NonData()

c = C()
c.v = 1
c.__dict__["n"] = "instance"
print(c.v)
print(c.n)
print(c.__dict__["v"])

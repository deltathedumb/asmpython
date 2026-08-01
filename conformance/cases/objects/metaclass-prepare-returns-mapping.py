# tier: spec
# ref: reference/datamodel.html#preparing-the-class-namespace
# expect:
# ['b', 'a']
# 1 2
class OrderedMeta(type):
    @classmethod
    def __prepare__(mcls, name, bases, **kw):
        return {}
    def __new__(mcls, name, bases, ns):
        cls = super().__new__(mcls, name, bases, dict(ns))
        cls.declared = [k for k in ns if not k.startswith("_")]
        return cls

class C(metaclass=OrderedMeta):
    b = 1
    a = 2

print(C.declared)
print(C.b, C.a)

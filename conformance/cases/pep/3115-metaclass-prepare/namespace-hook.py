# tier: spec
# ref: reference/datamodel.html#preparing-the-class-namespace
# expect:
# 42
# Meta
class Meta(type):
    @classmethod
    def __prepare__(mcls, name, bases, **kw):
        return {"injected": 42}

class C(metaclass=Meta):
    pass

print(C.injected)
print(type(C).__name__)

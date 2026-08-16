# guards: metaclass_compat_fixes
# expect:
# 2
# int
# str
class Field:
    def __init__(self, kind):
        self.kind = kind


class Meta(type):
    def __new__(mcls, name, bases, ns):
        collected = {}
        for key in ns:
            value = ns[key]
            if isinstance(value, Field):
                collected[key] = value.kind
        cls = super().__new__(mcls, name, bases, ns)
        cls._fields = collected
        return cls


class Model(metaclass=Meta):
    ident = Field("int")
    title = Field("str")

    @classmethod
    def fields(cls):
        return dict(cls._fields)


f = Model.fields()
print(len(f))
print(f["ident"])
print(f["title"])

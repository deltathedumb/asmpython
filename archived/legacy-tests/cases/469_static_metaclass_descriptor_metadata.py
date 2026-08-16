# expect:
# 2
# True
# True


class Descriptor:
    def __init__(self, default):
        self.default = default
        self.name = ""

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner=None):
        return self.default


class DescriptorMeta(type):
    def __new__(mcls, name, bases, namespace):
        fields = {}
        for base in bases:
            fields.update(getattr(base, "__fields__", {}))
        for key, value in namespace.items():
            if isinstance(value, Descriptor):
                fields[key] = value
        cls = super().__new__(mcls, name, bases, namespace)
        cls.__fields__ = fields
        return cls


class Base(metaclass=DescriptorMeta):
    first = Descriptor(1)

    @classmethod
    def reflected_fields(cls):
        return dict(cls.__fields__)


class Child(Base):
    second = Descriptor(2)


child = Child()
fields = child.reflected_fields()
print(len(fields))
print("first" in fields)
print("second" in fields)

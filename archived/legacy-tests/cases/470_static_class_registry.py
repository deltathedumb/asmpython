# expect:
# example.Base
# 9
# True


class Registry:
    def __init__(self):
        self._types = {}
        self._names = {}

    def register(self, type_name, cls):
        self._types[type_name] = cls
        self._names[cls] = type_name
        return cls

    def resolve(self, type_name):
        return self._types.get(type_name)

    def create(self, type_name, value=0):
        cls = self.resolve(type_name)
        if cls is None:
            return None
        return cls(value=value)

    def type_name(self, value_or_class):
        cls = value_or_class if isinstance(value_or_class, type) else type(value_or_class)
        return self._names.get(cls, "unknown")


REGISTRY = Registry()


def register_type(type_name, registry=REGISTRY):
    def decorate(cls):
        registry.register(type_name, cls)
        return cls

    return decorate


@register_type("example.Base")
class Base:
    def __init__(self, value=1):
        self.value = value


@register_type("example.Child")
class Child(Base):
    pass


base = Base()
child = REGISTRY.create("example.Child", value=9)
print(REGISTRY.type_name(base))
print(child.value)
print(REGISTRY.resolve("example.Base") is Base)

# guards: class_registry_compat_fixes
# expect:
# widget 12
# True
class Registry:
    def __init__(self):
        self._types = {}

    def register(self, name, cls):
        self._types[name] = cls
        return cls

    def create(self, name, value):
        cls = self._types.get(name)
        if cls is None:
            return None
        return cls(value)


class Widget:
    def __init__(self, value):
        self.value = value

    def show(self):
        return "widget " + str(self.value)


REGISTRY = Registry()
REGISTRY.register("widget", Widget)
made = REGISTRY.create("widget", 12)
print(made.show())
print(REGISTRY.create("missing", 1) is None)

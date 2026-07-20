# expect:
# 42
# 43


class Value:
    def __init__(self, number: int) -> None:
        self.number: int = number

    def read(self) -> int:
        return self.number


class Descriptor:
    def __init__(self, default, value_type=None) -> None:
        self.default = default
        self.name = ""

    def __set_name__(self, owner, name) -> None:
        self.name = name

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return instance.values.get(self.name, self.default)

    def __set__(self, instance, value) -> None:
        instance.values[self.name] = value


class Model:
    value = Descriptor(Value(42), value_type=Value)

    def __init__(self) -> None:
        self.values = {}


model = Model()
print(model.value.read())
model.value = Value(43)
print(model.value.read())

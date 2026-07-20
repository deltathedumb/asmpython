# expect:
# 7
# 42
# 42


class Descriptor:
    def __init__(self, default):
        self.default = default
        self.name = ""

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner=None):
        return instance.values.get(self.name, self.default)

    def __set__(self, instance, value):
        instance.values[self.name] = value


class Item:
    value = Descriptor(7)

    def __init__(self):
        self.values = {}


item = Item()
print(item.value)
item.value = 42
print(item.value)
print(item.values["value"])

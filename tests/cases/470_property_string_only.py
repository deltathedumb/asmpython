# expect:
# somnia.Root
# Method/property isolation generation 1.


class Registry:
    def __init__(self):
        self.names = {"Root": "somnia.Root"}

    def type_name(self, value):
        return self.names.get(value.name, value.name)


REGISTRY = Registry()


class Node:
    def __init__(self, name):
        self.name = name

    @property
    def type_name(self):
        return REGISTRY.type_name(self)


root = Node("Root")
print(root.type_name)

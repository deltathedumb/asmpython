# expect:
# 2
# somnia.Root
# Property-flow verification generation 1.


class Registry:
    def __init__(self):
        self.names = {
            "Root": "somnia.Root",
            "Child": "somnia.Child",
        }

    def type_name(self, value):
        return self.names.get(
            value.name,
            getattr(value, "fallback_name", value.name),
        )


REGISTRY = Registry()


class Node:
    def __init__(self, name):
        self.name = name
        self.children = []

    @property
    def type_name(self):
        return REGISTRY.type_name(self)

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


root = Node("Root")
root.children.append(Node("Child"))
values = [
    obj.type_name
    for obj in root.walk()
    if obj.type_name.startswith("somnia.")
]
print(len(values))
print(values[0])

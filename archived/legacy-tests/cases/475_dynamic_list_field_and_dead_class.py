# expect:
# 2


class Node:
    def __init__(self) -> None:
        self.children = []

    def add(self, child) -> None:
        self.children.append(child)

    def walk(self):
        yield self
        for child in list(self.children):
            yield from child.walk()


class UnusedOptionalBackend:
    def open(self):
        return missing_optional_runtime().open()


root = Node()
root.add(Node())
print(len(root.walk()))

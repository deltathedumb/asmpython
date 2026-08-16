# expect:
# 1

class Node:
    def __init__(self, name):
        self.name = name
        self.children = []

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()


root = Node("somnia.Root")
print([node.name.startswith("somnia.") for node in root.walk()][0])
